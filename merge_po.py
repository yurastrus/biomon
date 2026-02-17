import re

BIOMON_PO = r'translations/en/LC_MESSAGES/messages.po'
MYPROJECT_PO = r'C:/Users/IuriiStrus/repositories/myproject/translations/en/LC_MESSAGES/messages.po'

def parse_po_file(filepath):
    """Парсить .po файл і повертає словник {msgid: msgstr}"""
    translations = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Регулярний вираз для пошуку блоків msgid та msgstr (включаючи багаторядкові)
    pattern = re.compile(r'msgid\s+((?:"(?:\\.|[^"\\])*"\s*)+)\s+msgstr\s+((?:"(?:\\.|[^"\\])*"\s*)+)', re.MULTILINE)
    
    for match in pattern.finditer(content):
        # Очищаємо лапки та з'єднуємо рядки
        msgid = "".join(line.strip()[1:-1] for line in match.group(1).split('\n') if line.strip())
        msgstr = "".join(line.strip()[1:-1] for line in match.group(2).split('\n') if line.strip())
        
        if msgid and msgstr:
            translations[msgid] = msgstr
    return translations

def update_po_file():
    print(f"Завантаження перекладів з MyProject...")
    old_trans = parse_po_file(MYPROJECT_PO)
    
    with open(BIOMON_PO, 'r', encoding='utf-8') as f:
        content = f.read()

    # Шукаємо всі блоки, де msgstr порожній (msgstr "")
    # Навіть якщо msgid багаторядковий
    pattern = re.compile(r'(msgid\s+((?:"(?:\\.|[^"\\])*"\s*)+))\s+msgstr\s+""(?!\n")', re.MULTILINE)
    
    updated_count = 0

    def replace_func(match):
        nonlocal updated_count
        full_msgid_block = match.group(1)
        # Збираємо чистий текст msgid для пошуку в словнику
        msgid_clean = "".join(line.strip()[1:-1] for line in match.group(2).split('\n') if line.strip())
        
        if msgid_clean in old_trans:
            updated_count += 1
            # Формуємо новий блок з перекладом (якщо переклад довгий, він вставиться одним рядком, 
            # але Babel при наступному оновленні сам його красиво розіб'є)
            return f'{full_msgid_block}\nmsgstr "{old_trans[msgid_clean]}"'
        return match.group(0)

    new_content = pattern.sub(replace_func, content)

    with open(BIOMON_PO, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"Готово! Оновлено багаторядкових та звичайних фраз: {updated_count}")

if __name__ == "__main__":
    update_po_file()
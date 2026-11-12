const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

const window = { PamSegmentCutter: { AUDIO_EXT: /\.(wav|flac|mp3)$/i } };
vm.runInNewContext(fs.readFileSync('app/pam/static/js/recording_index.js', 'utf8'), {
    window, navigator: {}, performance, setTimeout
});
const index = window.PamRecordingIndex;

class FileHandle {
    kind = 'file';

    constructor(name, contents = '') {
        this.name = name;
        this.contents = contents;
    }

    async getFile() {
        return { name: this.name, text: async () => this.contents };
    }

    async createWritable() {
        let pending;
        return {
            write: async value => { pending = value; },
            close: async () => { this.contents = pending; },
            abort: async () => {}
        };
    }
}

class Directory {
    kind = 'directory';
    walks = 0;

    constructor(name, ...children) {
        this.name = name;
        this.children = new Map(children.map(child => [child.name, child]));
    }

    async *values() {
        this.walks++;
        yield* this.children.values();
    }

    async getDirectoryHandle(name) {
        return this.get(name, 'directory');
    }

    async getFileHandle(name, options) {
        if (options?.create && !this.children.has(name)) this.children.set(name, new FileHandle(name));
        return this.get(name, 'file');
    }

    get(name, kind) {
        const child = this.children.get(name);
        if (!child) throw new DOMException(name, 'NotFoundError');
        if (child.kind !== kind) throw new DOMException(name, 'TypeMismatchError');
        return child;
    }
}

const segment = (filename, location_id) => ({ recording_filename: filename, location_id });
const lookup = async names => Object.fromEntries(names.filter(name => /^[12]/.test(name)).map(name =>
    [name, { location_id: Number(name[0]), lat: 49.9, lon: 23.7 }]
));
const progress = () => {};

test('commits each folder before continuing, resumes and skips even empty markers', async () => {
    const unknown = new Directory('unknown', new FileHandle('unknown.wav'));
    const mixed = new Directory('mixed', new FileHandle('1_a.wav'), new FileHandle('2_b.wav'));
    const root = new Directory('archive', unknown, new Directory('nested', mixed));
    const plan = [segment('1_a.wav', 1), segment('2_b.wav', 2)];
    await assert.rejects(index.find(root, plan, async names => {
        if (names.includes('2_b.wav')) throw Error('interrupted');
        return lookup(names);
    }, progress), /interrupted/);
    assert.deepEqual(JSON.parse(unknown.children.get('location.json').contents).locations, []);
    assert.equal(mixed.children.has('location.json'), false);
    const unknownWalks = unknown.walks;
    const calls = [];
    const resumed = await index.find(root, plan, async names => {
        calls.push(names);
        return lookup(names);
    }, progress);
    assert.deepEqual(calls.map(names => [...names]), [['1_a.wav', '2_b.wav']]);
    assert.equal(unknown.walks, unknownWalks);
    assert.equal(resumed.files.size, 2);
    assert.deepEqual(JSON.parse(mixed.children.get('location.json').contents).locations
        .map(location => location.location_id), [1, 2]);

    const walks = root.walks + mixed.walks + unknown.walks;
    mixed.children.set('1_new.wav', new FileHandle('1_new.wav'));
    const cached = await index.find(root, [segment('1_new.wav', 1)], () => {
        assert.fail('unexpected lookup');
    }, progress);
    assert.equal(cached.files.size, 1);
    assert.equal(root.walks + mixed.walks + unknown.walks, walks);
});

test('repairs stale folder paths and updates a previously indexed mixed folder', async () => {
    const source = new Directory('before', new FileHandle('1_a.wav'));
    const destination = new Directory('destination', new FileHandle('2_b.wav'));
    const root = new Directory('archive', source, destination);
    const plan = [segment('1_a.wav', 1)];
    await index.find(root, plan, lookup, progress);
    destination.children.set('1_a.wav', source.children.get('1_a.wav'));
    source.children.delete('1_a.wav');
    root.children.delete('before');
    const result = await index.find(root, plan, lookup, progress);
    assert.equal(result.files.size, 1);
    assert.equal(result.missingCount, 0);
    assert.deepEqual(JSON.parse(destination.children.get('location.json').contents).locations
        .map(location => location.location_id), [1, 2]);
    assert.equal(JSON.parse(root.children.get('.pam-location-folders-v1.json').contents).folders.length, 1);
});

test('rebuilds an interrupted marker and reports duplicate candidate files', async () => {
    const first = new Directory('first', new FileHandle('1_a.wav'),
        new FileHandle('location.json', '{"pam_version":'));
    const second = new Directory('second', new FileHandle('1_a.wav'));
    const root = new Directory('archive', first, second);
    const result = await index.find(root, [segment('1_a.wav', 1)], lookup, progress);
    assert.equal(result.ambiguous, 1);
    assert.equal(result.files.size, 0);
    assert.equal(JSON.parse(first.children.get('location.json').contents).locations[0].location_id, 1);
});

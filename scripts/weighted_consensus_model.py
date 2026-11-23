"""Weighted-vote consensus for camera-trap series: model, penalties, rules.

Research code for scripts/probe_weighted_consensus.py (read-only study,
2026-11-23). Nothing here is used by the site yet.

Question it serves: if a verifier's vote is weighted by their record, how
many votes does a series need, and how often does it close on the wrong
species, compared with today's rule (two people agree)?

Model (one-coin Dawid-Skene with a shared confusion pattern):
  P(user u says s | true class c) = r(u, c)                 if s == c
                                  = (1 - r(u, c)) * Q[c, s] otherwise
  r(u, c) is the user's recall on class c ("when it really is roe deer,
  how often does u say roe deer"), Q[c, s] is where everybody's mistakes on
  c go (roe deer -> red deer far more than roe deer -> lynx).
  Posterior of a series: P(c | votes) ~ prior(c) * prod_u P(vote_u | c).

Training labels are leave-one-out: a vote is graded against the plain
majority of the OTHER voters of that series; no unique majority, no grade.

Small samples are the point of the penalty variants: 10 of 10 correct says
little, so r(u, c) is estimated as
  raw         H / N (no penalty, the control),
  wilson      lower bound of the Wilson interval (z),
  beta_class  (H + m * r_class) / (N + m), shrunk to the class average,
  beta_skill  same, but the prior is the class average moved by the user's
              overall skill on the logit scale (a strong verifier new to
              a species starts above the average, a novice below).
  beta_novice as beta_skill, but the user's overall skill is shrunk (m_user)
              towards the pooled record of NOVICES (verifiers with fewer than
              novice_n graded votes), not towards the vote-weighted average,
              which experts dominate. A newcomer starts as an average
              newcomer, not as an average vote.
Unseen (user, class) pairs get the prior itself (wilson: r_floor).
"""
import math
from collections import Counter, defaultdict

import numpy as np

UNIDENTIFIABLE = -6
UNIDENTIFIABLE_MIN_VOTERS = 5


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------

def majority(votes):
    """Unique top species by number of voters, or None (no votes or a tie)."""
    counts = Counter(s for _u, s in votes)
    if not counts:
        return None
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def loo_records(votes):
    """(user, truth, said) for every vote whose others have a unique majority."""
    out = []
    for i, (u, s) in enumerate(votes):
        truth = majority(votes[:i] + votes[i + 1:])
        if truth is not None:
            out.append((u, truth, s))
    return out


class Stats:
    """Counts behind every estimate, built from a list of series (vote lists)."""

    def __init__(self, series_votes):
        self.N = defaultdict(Counter)        # N[u][c] times truth was c
        self.H = defaultdict(Counter)        # H[u][c] ... and u said c
        self.C = defaultdict(Counter)        # C[c][s] mistakes c -> s, everyone
        self.T = Counter()                   # series whose majority is c
        for votes in series_votes:
            for u, truth, said in loo_records(votes):
                self.N[u][truth] += 1
                if said == truth:
                    self.H[u][truth] += 1
                else:
                    self.C[truth][said] += 1
            top = majority(votes)
            if top is not None:
                self.T[top] += 1

    def user_totals(self, u):
        return sum(self.H[u].values()), sum(self.N[u].values())

    def class_totals(self):
        n, h = Counter(), Counter()
        for u in self.N:
            for c, k in self.N[u].items():
                n[c] += k
                h[c] += self.H[u][c]
        return h, n


# --------------------------------------------------------------------------
# small-sample penalties
# --------------------------------------------------------------------------

def logit(p):
    return math.log(p / (1 - p))


def expit(x):
    return 1 / (1 + math.exp(-x))


def wilson_lower(h, n, z=1.96):
    if n == 0:
        return None
    p = h / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def shrink(h, n, prior, m):
    return (h + m * prior) / (n + m)


class Penalty:
    """How r(u, c) is estimated from H, N. See the module docstring."""

    def __init__(self, kind='beta_skill', m=10.0, z=1.96, r_min=0.3, r_max=0.99,
                 r_floor=0.5, m_class=20.0, m_user=50.0, novice_n=200):
        self.kind, self.m, self.z = kind, m, z
        self.m_user, self.novice_n = m_user, novice_n
        self.r_min, self.r_max, self.r_floor, self.m_class = r_min, r_max, r_floor, m_class

    def label(self):
        if self.kind == 'raw':
            return f'raw cap={self.r_max}'
        if self.kind == 'wilson':
            return f'wilson z={self.z} cap={self.r_max}'
        if self.kind == 'beta_novice':
            return f'novice m={self.m:g} mu={self.m_user:g} cap={self.r_max}'
        return f'{self.kind} m={self.m:g} cap={self.r_max}'

    def clamp(self, r):
        return min(self.r_max, max(self.r_min, r))


# --------------------------------------------------------------------------
# fitted model
# --------------------------------------------------------------------------

class Model:
    """r(u, c) table, confusion Q, log prior; all as numpy over a class index."""

    def __init__(self, stats, classes, penalty, alpha=1.0, prior_floor=1.0):
        self.classes = list(classes)
        self.idx = {c: i for i, c in enumerate(self.classes)}
        K = len(self.classes)
        self.penalty = penalty
        hc, nc = stats.class_totals()
        H_all, N_all = sum(hc.values()), sum(nc.values())
        self.g = (H_all + 1) / (N_all + 2) if N_all else 0.9
        # class recall, shrunk to the global rate
        hn = nn = 0
        for u in stats.N:
            h, n = stats.user_totals(u)
            if n < penalty.novice_n:
                hn, nn = hn + h, nn + n
        self.g_novice = (hn + 1) / (nn + 2) if nn else self.g
        self.r_class = np.array([shrink(hc[c], nc[c], self.g, penalty.m_class)
                                 for c in self.classes])

        # confusion pattern: where mistakes on c go, smoothed by answer frequency
        said_freq = np.array([stats.T[c] + 1.0 for c in self.classes])
        said_freq /= said_freq.sum()
        Q = np.zeros((K, K))
        for c in self.classes:
            i = self.idx[c]
            row = alpha * said_freq.copy()
            for s, k in stats.C[c].items():
                if s in self.idx:
                    row[self.idx[s]] += k
            row[i] = 0.0
            Q[i] = row / row.sum()
        self.logQ = np.log(np.maximum(Q, 1e-12))

        prior = np.array([stats.T[c] + prior_floor for c in self.classes], float)
        self.logpi = np.log(prior / prior.sum())

        self.stats = stats
        self._rows = {}
        self.default_row = self._row(None)

    def _row(self, u):
        """r(u, .) over all classes, already penalised and clamped."""
        p = self.penalty
        st = self.stats
        hu, nu = st.user_totals(u) if u is not None else (0, 0)
        if p.kind in ('beta_skill', 'beta_novice'):
            if p.kind == 'beta_skill':
                ru = shrink(hu, nu, self.g, p.m)
            else:
                ru = shrink(hu, nu, self.g_novice, p.m_user)
            shift = logit(min(0.999, max(0.001, ru))) - logit(self.g)
        out = np.empty(len(self.classes))
        for i, c in enumerate(self.classes):
            h = st.H[u][c] if u is not None else 0
            n = st.N[u][c] if u is not None else 0
            rc = float(self.r_class[i])
            if p.kind == 'raw':
                r = h / n if n else rc
            elif p.kind == 'wilson':
                lb = wilson_lower(h, n, p.z)
                r = lb if lb is not None else p.r_floor
            elif p.kind == 'beta_class':
                r = shrink(h, n, rc, p.m)
            elif p.kind in ('beta_skill', 'beta_novice'):
                prior = expit(logit(min(0.999, max(0.001, rc))) + shift)
                r = shrink(h, n, prior, p.m)
            else:
                raise ValueError(p.kind)
            out[i] = p.clamp(r)
        return out

    def row(self, u):
        if u not in self._rows:
            known = u in self.stats.N
            self._rows[u] = self._row(u) if known else self.default_row
        return self._rows[u]

    def vote_loglik(self, u, s):
        """log P(u says s | c) for every class c."""
        r = self.row(u)
        if s not in self.idx:
            return np.zeros(len(self.classes))
        j = self.idx[s]
        ll = np.log1p(-r) + self.logQ[:, j]
        ll[j] = math.log(r[j])
        return ll

    def log_posterior(self, votes):
        lp = self.logpi.copy()
        for u, s in votes:
            lp += self.vote_loglik(u, s)
        return lp - np.logaddexp.reduce(lp)

    def predict_vote(self, logpost, u):
        """P(next voter u says s) over the class index."""
        post = np.exp(logpost)
        r = self.row(u)
        # off-diagonal: sum_c post[c] (1 - r[c]) Q[c, s]; diagonal: post[s] r[s]
        Q = np.exp(self.logQ)
        pred = (post * (1 - r)) @ Q + post * r
        return pred / pred.sum()


# --------------------------------------------------------------------------
# closing rules: each returns the closing species for a vote prefix, or None
# --------------------------------------------------------------------------

def rule_current(votes, min_voters=2):
    """Today's rule for single-species voters (consensus.can_close)."""
    n = len(votes)
    if n < min_voters:
        return None
    counts = Counter(s for _u, s in votes)
    top, k = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if k * 2 <= n:
        return None
    if top == UNIDENTIFIABLE and n < UNIDENTIFIABLE_MIN_VOTERS:
        return None
    return top


def rule_sum(model, votes, threshold=1.96, min_voters=2):
    """The owner's first idea: add each supporter's accuracy; the first
    species to reach the threshold wins (if it is also the leader)."""
    n = len(votes)
    if n < min_voters:
        return None
    score = defaultdict(float)
    for u, s in votes:
        if s in model.idx:
            score[s] += float(model.row(u)[model.idx[s]])
        else:
            score[s] += model.g
    top = max(score.items(), key=lambda kv: kv[1])
    if top[1] < threshold:
        return None
    if top[0] == UNIDENTIFIABLE and n < UNIDENTIFIABLE_MIN_VOTERS:
        return None
    return top[0]


def rule_posterior(model, votes, tau=0.99, min_voters=2, min_support=2):
    """Close when the model is at least `tau` sure of the leader.

    Guards found necessary on real data (2026-11-23): at least `min_support`
    people must have named the leader (otherwise one lynx vote against one
    "other species" closed as lynx), and "not identifiable" keeps today's
    rule on top (five voters AND a plain majority): it is an answer, not an
    animal, and a confident model would otherwise close it against a crowd.
    """
    n = len(votes)
    if n < min_voters:
        return None
    lp = model.log_posterior(votes)
    i = int(np.argmax(lp))
    if math.exp(lp[i]) < tau:
        return None
    top = model.classes[i]
    support = sum(1 for _u, s in votes if s == top)
    if support < min_support:
        return None
    if top == UNIDENTIFIABLE and rule_current(votes) != UNIDENTIFIABLE:
        return None
    return top


def first_close(rule, votes):
    """(k, species) at the first prefix the rule closes, or (None, None)."""
    for k in range(1, len(votes) + 1):
        sp = rule(votes[:k])
        if sp is not None:
            return k, sp
    return None, None


# --------------------------------------------------------------------------
# held-out scoring
# --------------------------------------------------------------------------

def next_vote_logloss(model, series_votes, min_prefix=1):
    """Mean -log P(actual next vote | earlier votes) and the count scored.

    The honest test of a weighting: it must predict what the next person
    says, on series the model never saw. Also returns calibration pairs
    (predicted P(next == current posterior top), happened?).
    """
    total, n = 0.0, 0
    calib = []
    per_user = []
    for votes in series_votes:
        lp = model.logpi.copy()
        for k, (u, s) in enumerate(votes):
            if k >= min_prefix and s in model.idx:
                post = lp - np.logaddexp.reduce(lp)
                pred = model.predict_vote(post, u)
                loss = -math.log(max(pred[model.idx[s]], 1e-12))
                total += loss
                n += 1
                per_user.append((u, loss))
                top = int(np.argmax(post))
                calib.append((float(pred[top]), model.classes[top] == s))
            lp = lp + model.vote_loglik(u, s)
    model.last_per_user = per_user
    return (total / n if n else float('nan')), n, calib


def simulate(model, world, series_votes, rule, voter_pool, rng, draws=1, cap=10):
    """Close every series with `rule`; votes beyond the real ones are simulated.

    world: the model used as ground truth (true class drawn from its
    posterior given ALL real votes; extra voters answer from its likelihood).
    voter_pool: (users, probabilities) to draw extra voters from.
    Returns dict with mean votes used, error rate, share needing extra votes,
    share never closed within `cap`.
    """
    users, probs = voter_pool
    K = len(world.classes)
    Qw = np.exp(world.logQ)
    tot_votes = tot_err = tot_extra = tot_open = tot = 0
    for votes in series_votes:
        post_all = np.exp(world.log_posterior(votes))
        for _ in range(draws):
            c = rng.choice(K, p=post_all / post_all.sum())
            cur = []
            closed = None
            for k in range(cap):
                if k < len(votes):
                    cur.append(votes[k])
                else:
                    u = users[rng.choice(len(users), p=probs)]
                    while any(u == v[0] for v in cur):
                        u = users[rng.choice(len(users), p=probs)]
                    r = world.row(u)[c]
                    if rng.random() < r:
                        s = world.classes[c]
                    else:
                        s = world.classes[rng.choice(K, p=Qw[c])]
                    cur.append((u, s))
                sp = rule(cur)
                if sp is not None:
                    closed = sp
                    break
            tot += 1
            tot_votes += len(cur)
            tot_extra += len(cur) > len(votes)
            if closed is None:
                tot_open += 1
            elif closed != world.classes[c]:
                tot_err += 1
    return {'votes': tot_votes / tot, 'error': tot_err / tot,
            'extra': tot_extra / tot, 'open': tot_open / tot, 'n': tot}


# --------------------------------------------------------------------------
# The owner's agreed rule (2026-11-23): weighted votes, margin, DeepFaune
# --------------------------------------------------------------------------
#
#   human weight  = precision(u, s) * n / (n + 1)
#       precision: of the n times u said s, the share where the OTHER people
#       of that series (majority, without u and without the AI) also said s.
#       n = 0 -> weight 0 (no record on that species yet).
#   AI weight     = accuracy(s, score bin) * (1 - copy(s))
#       accuracy: share of series where the AI said s at that confidence and
#       the people's majority agreed (times n/(n+1) as for people).
#       copy(s): how much more often than usual people repeat the AI's s when
#       the AI was wrong (excess over the base rate, smoothed, in [0, 1]).
#   score(species) = sum of the weights of everybody who named it;
#   close when score(leader) - score(second) >= threshold AND at least one
#   person voted. "Not identifiable" keeps today's rule (5 voters, majority).

AI_USER = 'AI'
SCORE_BINS = (0.0, 0.7, 0.9, 0.95, 0.99, 1.01)


def score_bin(score):
    if score is None:
        return 0
    for i in range(len(SCORE_BINS) - 1):
        if SCORE_BINS[i] <= score < SCORE_BINS[i + 1]:
            return i
    return len(SCORE_BINS) - 2


def experience_factor(n):
    return n / (n + 1)


class AgreedWeights:
    """Weights of people and of the AI, learned from a list of series.

    series: iterable of (human_votes, ai) where human_votes is the ordered
    list of (user, species) and ai is None or (species, score, shown):
    shown[i] is True when the AI answer existed before human vote i (only
    those votes can show copying).
    """

    def __init__(self, series, copy_m=10.0):
        said = defaultdict(Counter)          # said[u][s]
        hit = defaultdict(Counter)           # hit[u][s]
        ai_n, ai_hit = Counter(), Counter()  # by (s, bin)
        # Copying. Every graded human vote has a truth (the others' majority)
        # and A = the AI's species if the AI was shown before that vote.
        #   copies[s] / wrong[s]: A == s != truth, and the person said s
        #   base[s] / base_den[s]: A != s, truth != s, and the person said s
        wrong, copies = Counter(), Counter()
        base = Counter()
        n_all = 0
        n_truth, n_shown, n_shown_truth = Counter(), Counter(), Counter()
        for votes, ai in series:
            ai_sp = ai[0] if ai is not None else None
            for i, (u, s) in enumerate(votes):
                ai_first = bool(ai is not None and ai[2][i])
                truth = majority(votes[:i] + votes[i + 1:])
                if truth is None:
                    continue
                said[u][s] += 1
                hit[u][s] += (s == truth)
                shown = ai_sp if (ai_first and ai_sp is not None) else None
                # Copying needs a truth that does not hinge on one person: with
                # a single other voter, whoever sided with a correct AI would
                # look like a copier. So at least two others and a strict
                # majority among them.
                others = votes[:i] + votes[i + 1:]
                if len(others) < 2 or                         sum(1 for _u, x in others if x == truth) * 2 <= len(others):
                    continue
                n_all += 1
                n_truth[truth] += 1
                if shown is not None:
                    n_shown[shown] += 1
                    if shown == truth:
                        n_shown_truth[shown] += 1
                    if shown != truth:
                        wrong[shown] += 1
                        copies[shown] += (s == shown)
                if s != truth and s != shown:
                    base[s] += 1
            truth_all = majority(votes)
            if ai_sp is not None and truth_all is not None:
                key = (ai_sp, score_bin(ai[1]))
                ai_n[key] += 1
                ai_hit[key] += (ai_sp == truth_all)
        self.said, self.hit = said, hit
        self.ai_n, self.ai_hit = ai_n, ai_hit

        def base_rate(x):
            den = n_all - n_truth[x] - n_shown[x] + n_shown_truth[x]
            return base[x] / den if den > 0 else 0.0

        all_w = sum(wrong.values())
        all_c = sum(copies.values())
        all_b = (sum(base_rate(x) * wrong[x] for x in wrong) / all_w) if all_w else 0.0
        self.copy_all = self._excess(all_c / all_w if all_w else 0.0, all_b)
        self.copy, self.copy_raw = {}, {}
        for x, w in wrong.items():
            b = base_rate(x)
            c = self._excess(copies[x] / w, b)
            self.copy_raw[x] = (copies[x], w, b, c)
            self.copy[x] = (c * w + copy_m * self.copy_all) / (w + copy_m)

    @staticmethod
    def _excess(r, b):
        if b >= 1:
            return 0.0
        return min(1.0, max(0.0, (r - b) / (1 - b)))

    def human(self, u, s):
        n = self.said[u][s] if u in self.said else 0
        if n == 0:
            return 0.0
        return self.hit[u][s] / n * experience_factor(n)

    def ai(self, s, score):
        if s is None:
            return 0.0
        key = (s, score_bin(score))
        n = self.ai_n[key]
        if n == 0:
            return 0.0
        acc = self.ai_hit[key] / n * experience_factor(n)
        return acc * (1 - self.copy.get(s, self.copy_all))


def ai_vote_weight(weights, s, score, mode='copy', half=0.5):
    """DeepFaune's vote under the variants discussed on 2026-11-23.

    copy        accuracy(s, score bin) * (1 - copy rate)       (first version)
    score_half  raw score * 0.5                                 (owner's simple one)
    acc_half    accuracy(s, score bin) * n/(n+1) * 0.5          (simple, but
                checked against people: a confident label the people reject,
                e.g. 'horse', gets little weight)
    """
    if s is None:
        return 0.0
    if mode == 'copy':
        return weights.ai(s, score)
    if mode == 'score_half':
        return (score or 0.0) * half
    if mode == 'acc_half':
        key = (s, score_bin(score))
        n = weights.ai_n[key]
        return weights.ai_hit[key] / n * experience_factor(n) * half if n else 0.0
    raise ValueError(mode)


def rule_margin(weights, votes, ai=None, threshold=1.5, use_ai=True, ai_mode='score_half',
                ai_named_only=True):
    """Species the series closes on, or None. votes = human (user, species).

    ai_named_only: the AI's points count only for a species some person named,
    so the AI can help a species but never pushes against two people with a
    label of its own (e.g. 'horse' for a kulan it does not know, 'empty' on a
    roe deer it missed). Added 2026-11-23 after the run on finished series;
    owner fixed it as part of the rule the same day (default True), together
    with ai_mode='score_half' and threshold 1.5.
    """
    if not votes:
        return None
    score = defaultdict(float)
    for u, s in votes:
        score[s] += weights.human(u, s)
    backs_named = not ai_named_only or (ai is not None and ai[0] in score)
    if use_ai and ai is not None and ai[0] is not None and backs_named:
        score[ai[0]] += ai_vote_weight(weights, ai[0], ai[1], ai_mode)
    ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
    lead = ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0)
    if lead < threshold:
        return None
    top = ranked[0][0]
    if top not in {s for _u, s in votes}:
        return None          # the AI alone never closes a series
    if top == UNIDENTIFIABLE and rule_current(votes) != UNIDENTIFIABLE:
        return None
    return top

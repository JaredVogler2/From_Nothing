"""Exact Shapley, sequential and proportional allocations for the worked attribution examples of 04 (illustrative money)."""
from fractions import Fraction as F
from itertools import combinations, permutations
from math import factorial


def shapley(players, v):
    n = len(players)
    out = {}
    for i in players:
        others = [p for p in players if p != i]
        total = F(0)
        for k in range(n):
            for S in combinations(others, k):
                w = F(factorial(k) * factorial(n - k - 1), factorial(n))
                total += w * (v(frozenset(S) | {i}) - v(frozenset(S)))
        out[i] = total
    return out


def sequential(order, v):
    out, done = {}, frozenset()
    for i in order:
        out[i] = v(done | {i}) - v(done)
        done = done | {i}
    return out


def proportional(players, v):
    single = {i: v(frozenset({i})) for i in players}
    tot = sum(single.values())
    whole = v(frozenset(players))
    if tot == 0:
        return {i: whole / len(players) for i in players}
    return {i: whole * single[i] / tot for i in players}


rate = F(300_000)          # $/day, illustrative
shift = F(8, 24) * rate    # one shift of delivery delay = $100,000


# Example D1: A -> B on one critical chain of late aircraft 1301, plus C on aircraft 1302 (independent, $50,000 if deferred).
def v1(S):
    cost = F(0)
    if S & {"A", "B"}:
        cost += shift                       # any of A, B missing slips 1301 one shift (not two)
    if "C" in S:
        cost += F(50_000)
    return cost


players = ["A", "B", "C"]
print("D1 singles", {p: float(v1(frozenset({p}))) for p in players}, "sum", float(sum(v1(frozenset({p})) for p in players)),
      "joint", float(v1(frozenset(players))))
print("D1 shapley", {k: float(x) for k, x in shapley(players, v1).items()})
print("D1 sequential A,B,C", {k: float(x) for k, x in sequential(["A", "B", "C"], v1).items()},
      "B,A,C", {k: float(x) for k, x in sequential(["B", "A", "C"], v1).items()})
print("D1 proportional", {k: float(x) for k, x in proportional(players, v1).items()})


# Example D2: complementary paths: Z needs P and Q; each alone has 1 shift of float, both missing slips Z one shift.
def v2(S):
    return shift if {"P", "Q"} <= S else F(0)


print("D2 singles", {p: float(v2(frozenset({p}))) for p in "PQ"}, "joint", float(v2(frozenset("PQ"))),
      "shapley", {k: float(x) for k, x in shapley(list("PQ"), v2).items()},
      "proportional", {k: float(x) for k, x in proportional(list("PQ"), v2).items()},
      "sequential P,Q", {k: float(x) for k, x in sequential(list("PQ"), v2).items()})


# Realized value of completing a subset S of the at-risk set N: v(N) - v(N \ S) versus the sum of Shapley shares of S.
N = frozenset(players)
phi = shapley(players, v1)
for S in [{"A"}, {"B"}, {"A", "B"}, {"A", "C"}]:
    S = frozenset(S)
    print("D1 completing", sorted(S), "avoided", float(v1(N) - v1(N - S)), "sum of shares", float(sum(phi[i] for i in S)))

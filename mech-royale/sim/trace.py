import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim as S
seed = int(sys.argv[1]); intel = sys.argv[2] if len(sys.argv) > 2 else "vision"
path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(S.ROOT, "bot.py")
s = S.Sim(seed, S.load_bot(path), intel); s.bot_ms = []
orig = s.bot_act
def wrapped():
    orig()
    if s.tick % 10 == 0 and s.mechs[0].alive:
        me = s.mechs[0]; zc, zr, _, _ = s.zone()
        near = sorted((math.hypot(m.pos[0]-me.pos[0], m.pos[1]-me.pos[1]), m.id) for m in s.mechs[1:] if m.alive)[:3]
        print(f"t={s.t:5.0f} hp={me.hp:5.0f} edge={zr-math.hypot(me.pos[0]-zc[0],me.pos[1]-zc[1]):5.0f} alive={sum(m.alive for m in s.mechs)} near={[(round(d),i) for d,i in near]} w1={me.slot1} dbg={getattr(s.bot,'dbg',None)} mv={[round(x,1) for x in me.cmd['move']]} fire={me.cmd['fire']}")
s.bot_act = wrapped
print(s.run())

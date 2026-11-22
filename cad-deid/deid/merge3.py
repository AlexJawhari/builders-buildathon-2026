"""Attempt-3 PDF-only dictionary: person/organisation names harvested from PDF body text (harvest_pdf.jsonl) with a vocabulary-based
filter (english words, corpus lower-case use, document frequency) instead of attempt 2's all-words-rare rule. The result is applied only
inside PDF text/metadata (never to paths or other formats) via MACHINA_PDF_EXTRA.
usage: python -m deid.merge3 harvest_pdf.jsonl df.json base_idents.json out.json"""
import collections, json, re, sys
from . import merge2, denylist
from .discover import english

FIRST = set('''james john robert michael william david richard joseph thomas charles daniel matthew anthony mark donald steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel frank gregory raymond alexander patrick jack dennis jerry tyler aaron jose adam henry nathan douglas zachary peter kyle walter ethan jeremy harold keith christian roger noah gerald carl terry sean austin arthur lawrence jesse dylan bryan joe jordan billy bruce albert willie gabriel logan alan juan wayne roy ralph randy eugene vincent russell elijah louis bobby philip johnny
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen nancy lisa betty margaret sandra ashley kimberly emily donna michelle dorothy carol amanda melissa deborah stephanie rebecca sharon laura cynthia kathleen amy angela shirley anna brenda pamela emma nicole helen samantha katherine christine debra rachel carolyn janet catherine maria heather diane ruth julie olivia joyce virginia victoria kelly lauren christina joan evelyn judith megan andrea cheryl hannah jacqueline martha gloria teresa ann sara madison frances kathryn janice jean abigail alice julia judy sophia grace denise amber doris marilyn danielle beverly isabella theresa diana natalie brittany charlotte marie kayla alexis lori
hans peter klaus wolfgang jürgen günter dieter uwe markus stefan andreas thomas michael christian matthias martin tobias florian sebastian jan lukas felix maximilian georg franz josef karl heinz horst werner helmut manfred bernd gerhard rainer ulrich walter erich otto fritz anton johann max moritz paul simon leon ludwig bernhard laurens marianne ursula monika petra sabine renate brigitte gisela helga ingrid christa karin birgit andrea claudia susanne anja nicole kerstin
jean pierre michel philippe alain jacques bernard marcel rené louis françois nicolas laurent olivier stéphane christophe sébastien frédéric thierry eric patrick guillaume julien mathieu antoine cyrille marie nathalie isabelle sylvie catherine françoise monique sophie céline valérie
carlos antonio manuel francisco luis miguel javier jesús pedro pablo alejandro fernando sergio ángel rafael daniel diego jorge alberto andrés raúl enrique ramón víctor roberto ignacio marco mario giuseppe giovanni luca alessandro andrea stefano paolo davide matteo lorenzo simone fabio claudio massimo
wei li wang zhang liu chen yang huang zhao wu zhou xu sun ma zhu hu guo he lin gao luo zheng liang xie song tang han feng deng cao peng zeng xiao tian dong pan yuan cai jiang yu du ye cheng su wei lu ding ren shen yao lu jiang cui zhong tan
ahmed mohammed muhammad ali omar hassan hussein ibrahim yusuf khalid mustafa abdul raj rahul amit sanjay vijay anil sunil ravi suresh ramesh rajesh priya anita sunita kavita deepak arun ashok vikram manoj girish
sergey ivan dmitry alexey andrey vladimir nikolay oleg igor yuri pavel maxim denis anton viktor
hiroshi takashi kenji yuki akira satoshi masashi takeshi taro kazuo yoshi sven lars erik nils anders johan bjorn henrik magnus olof jens kasper mikkel
fabian marianne bryan elliott luke menno tomas andrzej'''.split())
INST = set('university institute institution laboratory laboratories college school hospital foundation association society council ministry department division group labs lab technologies technology inc llc ltd limited gmbh ag sa bv corp corporation company co systems industries instruments engineering consortium academy center centre federation observatory agency authority committee'.split())
CAPW = re.compile(r"^(?:[A-ZÀ-Þ][\w'’-]*\.?|[A-ZÀ-Þ]\.)$")


def main(harvest, dfp, basep, out):
    df = json.load(open(dfp)); base = set(json.load(open(basep))); L = collections.Counter(); cnt = collections.Counter(); kinds = {}
    for l in open(harvest):
        d = json.loads(l); L.update(d.get('lw') or ())
        for kind, s in set(map(tuple, d.get('i') or ())):
            if kind in ('person', 'org', 'titleblock'): cnt[(kind, s)] += 1
    def common(t):
        t = t.lower().strip('.,')
        return t in english() or L.get(t, 0) >= 3 or df.get(t, 0) >= 80
    res = {}
    for (kind, s), n in cnt.items():
        a = merge2.accept(s, kind)
        if not a or a in base: continue
        toks = re.findall(r"[^\W\d_][\w'’.-]*", a)
        if not toks or not all(CAPW.match(t) for t in toks): continue
        core = [t for t in toks if t.lower().strip('.,') not in merge2.SUFFIX]
        inst = any(t.lower().strip('.,') in INST for t in toks)
        ok = False
        if kind == 'person' or (kind == 'titleblock' and len(toks) <= 3 and not inst):
            if 2 <= len(toks) <= 3 and len(toks[-1].strip('.')) >= 3:
                init = bool(re.match(r'^(?:[A-Z]\.)+$|^[A-Z]\.[A-Z]', toks[0]))
                first = toks[0].lower().strip('.') in FIRST
                ok = (init and not common(toks[-1])) or (first and len(toks[0]) >= 3) or all(not common(t) for t in toks if len(t.strip('.')) > 1)
        elif kind == 'org' or kind == 'titleblock':
            if len(toks) == 1:
                t = toks[0]
                ok = n >= 3 and len(t) >= 4 and not common(t) and not re.search(r'\d', t) and df.get(t.lower(), 0) <= 300
            elif len(toks) <= 5:
                ok = (inst and any(not common(t) for t in core)) or all(not common(t) for t in toks if len(t) > 1)
        if ok: res[a] = kind
        if kind == 'org' and len(toks) in (4, 5) and not inst:   # 'Georg Mikula Markus Pinter' is two names
            for i in range(len(toks) - 1):
                p = toks[i:i + 2]
                if all(len(t) >= 3 and not common(t) for t in p) or (p[0].lower() in FIRST and not common(p[1])): res[' '.join(p)] = 'person'
    json.dump(sorted(res), open(out, 'w'), ensure_ascii=False)
    print(len(res), collections.Counter(res.values()))


if __name__ == '__main__':
    main(*sys.argv[1:5])

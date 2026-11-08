"""助手名字：检查、读法（给唤醒模型）、在识别结果里认名字（给第二条唤醒路）。

用户可以在 Deck 设置里把助手改成任何名字（英文 3–12 个字母，或 2–4 个汉字），所以这里不能再写死某个名字的写法。

1. 读法（KWS 关键词要音素）：
   - 英文：先查唤醒模型自带的 CMU 词典 en.phone（12 万词，常见名字都在）；
   - 查不到（Kaia、Mochi 这类）：用一套简化的「字母 → 音素」规则猜 1–3 种读法。规则的思路来自
     NRL 字母发音规则（Elovitz et al. 1976，公有领域），只保留名字里常见的拼法；
   - 用户可以填「读法提示」：写汉字（维斯帕）就按拼音，写英文（vest per）就按单词拼起来，排在最前面。
2. 认名字（SenseVoice 把「你好 Kaia」写成「你好凯亚」「HI KAYA」「凯 YA」…）：
   把名字和识别结果都换成「粗音标」（同类辅音合并：V/W/F、P/B、T/D、K/G、S/Z…，元音只分 5 类），
   再算加权编辑距离。中文音译按拼音换。录入时 SenseVoice 认出的写法会记成别名，下次直接对上。
   词典里的常用词（WHISPER、WATER）要几乎一样才算，免得聊天时撞上。真正把关的仍是声纹。
"""
import itertools
import json
import re
import threading

try:
    from . import voicepaths as P
except ImportError:  # wake_cli.py 直接运行
    import voicepaths as P

CJK = re.compile(r"[一-鿿]")
_LATIN_NAME = re.compile(r"^[A-Za-z]{3,12}$")
_CJK_NAME = re.compile(r"^[一-鿿]{2,4}$")
# 这些叫起来和系统或常用说法撞车，不让用
_RESERVED = {"SIRI", "ALEXA", "GOOGLE", "CORTANA", "HEY", "HELLO", "OKAY", "STOP", "DECK", "MAC", "IPAD", "YES", "NO",
             "小爱", "小度", "天猫精灵", "小艺", "你好", "停下", "再见"}


def validate(name, hint=""):
    """返回 (规范化的名字, 规范化的读法提示, 错误)。错误为 None 才能用。"""
    n = (name or "").strip()
    h = re.sub(r"\s+", " ", (hint or "").strip())
    if _LATIN_NAME.match(n):
        n = n[0].upper() + n[1:].lower()
    elif not _CJK_NAME.match(n):
        return None, None, "名字只能是 3–12 个英文字母（一个词，不带空格），或 2–4 个汉字"
    if n.upper() in _RESERVED or n in _RESERVED:
        return None, None, "这个名字和别的语音助手或常用词撞了，换一个"
    if h and not re.fullmatch(r"[A-Za-z一-鿿 ']{1,24}", h):
        return None, None, "读法提示只写汉字或英文单词（例如：维斯帕、vest per），最多 24 个字"
    return n, h, None


# ---------------------------------------------------------------- 英文词典
class _Lexicon:
    """唤醒模型目录里的 en.phone（12 万词）。整份读成 dict 要多占 60 多 MB，这台 Mac 内存紧，
    所以只把原文（约 3 MB）放在内存里，查词时用正则在原文里找，查过的记下来。"""

    def __init__(self, text):
        self.text = text
        self.cache = {}
        self.lock = threading.Lock()

    def get(self, word, default=None):
        w = (word or "").upper()
        if not w or not re.fullmatch(r"[A-Z'.\-]+", w):
            return default
        with self.lock:
            if w not in self.cache:
                prons = []
                # str.find 比正则扫 3 MB 快得多（约 1 毫秒）：找「换行 + 单词 + 空格」和「换行 + 单词 + (2)」
                for pat in ("\n" + w + " ", "\n" + w + "\t", "\n" + w + "("):
                    i = self.text.find(pat)
                    while i >= 0:
                        end = self.text.find("\n", i + 1)
                        line = self.text[i + 1: end if end >= 0 else None].split()
                        if line and re.fullmatch(re.escape(w) + r"(\(\d+\))?", line[0]):
                            p = line[1:]
                            if p and p not in prons:
                                prons.append(p)
                        i = self.text.find(pat, i + 1)
                if len(self.cache) > 4000:
                    self.cache.clear()
                self.cache[w] = prons
            got = self.cache[w]
        return got if got else default

    def __contains__(self, word):
        return bool(self.get(word))


_lex = None
_lex_lock = threading.Lock()


def lexicon():
    """{单词: [[音素…], …]} 的只读查表（FRIDAY(1) 这种算同一个词）。模型没下好时查什么都是空的。"""
    global _lex
    with _lex_lock:
        if _lex is None:
            try:
                txt = (P.KWS_DIR / "en.phone").read_text(encoding="utf-8").upper()
            except OSError:
                txt = ""
            _lex = _Lexicon("\n" + txt)
        return _lex


# ---------------------------------------------------------------- 字母 → 音素（查不到的名字用）
_VOW = set("AEIOUY")
# 顺序很重要：长的拼法排前面。值是几种可能的读法（每种是音素列表；""=不发音）
_GRAPH = [
    ("TCH", [["CH"]]), ("SCH", [["SH"], ["S", "K"]]), ("PH", [["F"]]), ("TH", [["TH"]]), ("SH", [["SH"]]),
    ("CH", [["CH"], ["K"]]), ("CK", [["K"]]), ("QU", [["K", "W"]]), ("WH", [["W"]]), ("NG", [["NG"]]),
    ("DG", [["JH"]]), ("GH", [[]]), ("KH", [["K"]]), ("ZH", [["ZH"]]),
    ("AI", [["AY"], ["EY"]]), ("AY", [["EY"], ["AY"]]), ("EI", [["EY"], ["AY"]]), ("EY", [["EY"]]),
    ("EE", [["IY"]]), ("EA", [["IY"]]), ("IE", [["IY"]]), ("OO", [["UW"]]), ("OU", [["UW"], ["AW"]]),
    ("OW", [["OW"], ["AW"]]), ("OI", [["OY"]]), ("OY", [["OY"]]), ("AU", [["AO"]]), ("AW", [["AO"]]),
    ("UE", [["UW"]]), ("UI", [["UW", "IY"]]), ("IA", [["IY", "AH"]]), ("IO", [["IY", "OW"]]), ("EO", [["IY", "OW"]]),
    ("ER", [["ER"]]), ("IR", [["ER"]]), ("UR", [["ER"]]), ("YR", [["ER"]]), ("AR", [["AA", "R"]]), ("OR", [["AO", "R"]]),
]
_ONE = {"B": "B", "D": "D", "F": "F", "H": "HH", "K": "K", "L": "L", "M": "M", "N": "N", "P": "P", "Q": "K",
        "R": "R", "S": "S", "T": "T", "V": "V", "W": "W", "Z": "Z", "J": "JH"}
_V1 = {"A": [["AA"], ["AE"]], "E": [["EH"], ["IY"]], "I": [["IY"], ["IH"]], "O": [["OW"], ["AA"]],
       "U": [["UW"], ["AH"]], "Y": [["IY"]]}
_VFINAL = {"A": [["AH"]], "E": [["IY"]], "I": [["IY"]], "O": [["OW"]], "U": [["UW"]], "Y": [["IY"]]}
_VOWEL_PH = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}


def rules_g2p(word, limit=3):
    """英文名字按拼写猜读法，返回最多 limit 种（带重音：第一个元音 1，其余 0）。"""
    w = re.sub(r"[^A-Z]", "", word.upper())
    if not w:
        return []
    # 末尾不发音的 e（Jade、Rose）：前面是辅音、而且前面还有别的元音才算
    silent_e = len(w) >= 4 and w.endswith("E") and w[-2] not in _VOW and any(c in _VOW for c in w[:-2])
    if silent_e:
        w = w[:-1]
    slots = []   # 每个位置几种可能
    i = 0
    while i < len(w):
        c = w[i]
        if i + 1 < len(w) and w[i + 1] == c and c not in _VOW:   # 双写辅音只念一次
            i += 1
            continue
        hit = None
        for g, prons in _GRAPH:
            if w.startswith(g, i):
                # GH 在词首念 G；AR/OR/ER… 后面紧跟元音时 R 归下一个音节（Ari-a、Ora），不合并
                if g == "GH" and i == 0:
                    prons = [["G"]]
                if g[-1] == "R" and i + len(g) < len(w) and w[i + len(g)] in _VOW:
                    continue
                if g in ("IA", "IO", "EO") and i == 0:
                    continue
                hit = (g, prons)
                break
        if hit:
            slots.append(hit[1])
            i += len(hit[0])
            continue
        nxt = w[i + 1] if i + 1 < len(w) else ""
        last = i == len(w) - 1
        if c in _VOW:
            if c == "Y" and (i == 0 or (nxt and nxt in "AEIOU")):
                slots.append([["Y"]])
            elif last:
                slots.append(_VFINAL[c])
            else:
                slots.append(_V1[c])
        elif c == "C":
            slots.append([["S"]] if nxt in ("E", "I", "Y") else [["K"]])
        elif c == "G":
            slots.append([["JH"], ["G"]] if nxt in ("E", "I", "Y") else [["G"]])
        elif c == "X":
            slots.append([["Z"]] if i == 0 else [["K", "S"]])
        elif c == "K" and i == 0 and nxt == "N":
            slots.append([[]])
        elif c == "W" and i == 0 and nxt == "R":
            slots.append([[]])
        elif c in _ONE:
            slots.append([[_ONE[c]]])
        i += 1
    out = []
    for combo in itertools.product(*slots):
        ph = [p for part in combo for p in part]
        stressed = False
        res = []
        for p in ph:
            if p in _VOWEL_PH:
                res.append(p + ("1" if not stressed else "0"))
                stressed = True
            else:
                res.append(p)
        if res and res not in out:
            out.append(res)
        if len(out) >= limit:
            break
    return out


def word_prons(word, limit=3):
    """一个英文词的读法：词典优先，词典没有就按规则猜。"""
    lex = lexicon()
    got = list(lex.get(word.upper()) or [])[:limit]
    return got or rules_g2p(word, limit)


def pinyin_tokens(text):
    """汉字 → 唤醒模型用的声母 + 带调韵母（和官方 text2token 的 ppinyin 一致）。"""
    from pypinyin import pinyin
    from pypinyin.contrib.tone_convert import to_finals_tone, to_initials
    out = []
    for (py,) in pinyin(text):
        out += [x for x in (to_initials(py, strict=False), to_finals_tone(py, strict=False)) if x] or [py]
    return out


def hint_prons(hint):
    """读法提示 → 读法列表。汉字按拼音；英文单词逐个查（vest per → V EH1 S T + P ER1）。"""
    if not hint:
        return []
    parts = re.findall(r"[一-鿿]+|[A-Za-z']+", hint)
    choices = []
    for part in parts:
        if CJK.match(part):
            choices.append([pinyin_tokens(part)])
        else:
            pr = word_prons(part, 2)
            if not pr:
                return []
            choices.append(pr)
    return [[t for toks in combo for t in toks] for combo in itertools.product(*choices)][:3]


# ---------------------------------------------------------------- 粗音标 + 相似度
_CLS = {"P": "P", "B": "P", "F": "F", "V": "F", "W": "F", "T": "T", "D": "T", "K": "K", "G": "K", "S": "S", "Z": "S",
        "TH": "S", "DH": "S", "SH": "J", "ZH": "J", "CH": "J", "JH": "J", "M": "M", "N": "N", "NG": "N", "L": "L",
        "R": "R", "Y": "Y", "HH": "",
        "AA": "a", "AE": "a", "AH": "a", "AW": "a", "AO": "o", "OW": "o", "OY": "o", "EH": "e", "EY": "e",
        "IH": "i", "IY": "i", "AY": "i", "UW": "u", "UH": "u", "ER": "R"}
_PY_INIT = {"b": "P", "p": "P", "f": "F", "w": "F", "d": "T", "t": "T", "g": "K", "k": "K", "h": "", "j": "J", "q": "J",
            "x": "J", "zh": "J", "ch": "J", "sh": "J", "z": "S", "c": "S", "s": "S", "r": "R", "l": "L", "m": "M",
            "n": "N", "y": "Y"}


def _py_final(f):
    f = re.sub(r"\d", "", f).replace("ü", "v")
    if f in ("er",):
        return "R"
    tail = "N" if f.endswith(("n", "ng")) else ""
    core = re.sub(r"n?g?$", "", f) if tail else f
    if not core:
        return tail
    lead = core[0]
    if lead in "iuv" and len(core) > 1:     # ia ua uo ie ian → 主元音在后面
        core = core[1:]
    v = core[0]
    m = {"a": "a", "o": "o", "e": "e", "i": "i", "u": "u", "v": "u"}.get(v, "a")
    if core in ("ei", "ai"):
        m = "e" if core == "ei" else "i"
    if core in ("ao", "ou"):
        m = "o"
    return m + tail


def key_of_phones(ph):
    return "".join(_CLS.get(re.sub(r"\d", "", p), "") for p in ph)


def key_of_cjk(text):
    from pypinyin import Style, pinyin
    out = ""
    for (py,) in pinyin(text, style=Style.NORMAL, strict=False):
        m = re.match(r"(zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?(.*)", py)
        ini, fin = m.group(1) or "", m.group(2)
        out += _PY_INIT.get(ini, "") + _py_final(fin)
    return out


def keys_of_word(word):
    """一个英文词（可能是识别器乱拼的）可能的粗音标。"""
    return list(dict.fromkeys(key_of_phones(p) for p in word_prons(word, 3)))


def _is_vowel(c):
    return c in "aeiou"


def _sub(a, b):
    if a == b:
        return 0.0
    if _is_vowel(a) and _is_vowel(b):
        return 0.3
    if {a, b} == {"R", "L"} or {a, b} == {"S", "J"} or {a, b} == {"F", "P"}:
        return 0.45
    if "R" in (a, b) and (_is_vowel(a) or _is_vowel(b)):
        return 0.5      # PER 和 帕（pa）
    if {a, b} <= {"N", "M"}:
        return 0.4
    if "Y" in (a, b) and ({a, b} & {"i", "e"}):
        return 0.4      # KYA 和 KIA
    return 1.0


def _indel(c):
    return 0.4 if _is_vowel(c) or c in "RY" else 1.0


def similarity(a, b):
    """两个粗音标的相似度 0–1（加权编辑距离）。"""
    if not a or not b:
        return 0.0
    n, m = len(a), len(b)
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = d[i - 1][0] + _indel(a[i - 1])
    for j in range(1, m + 1):
        d[0][j] = d[0][j - 1] + _indel(b[j - 1])
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + _indel(a[i - 1]), d[i][j - 1] + _indel(b[j - 1]),
                          d[i - 1][j - 1] + _sub(a[i - 1], b[j - 1]))
    return max(0.0, 1.0 - d[n][m] / max(n, m))


# ---------------------------------------------------------------- 在识别结果里找名字
_GREET_MAX_CJK, _GREET_MAX_WORDS = 4, 2
_PUNCT = re.compile(r"[，。！？、,.!?;；:：\"'“”‘’()（）\-]")


def name_keys(name, hint=""):
    if CJK.search(name):
        ks = [key_of_cjk(name)]
    else:
        ks = [key_of_phones(p) for p in word_prons(name, 3)]
    for p in hint_prons(hint):
        ks.append(key_of_phones(p) if not CJK.search(hint) else key_of_cjk(hint))
    return [k for k in dict.fromkeys(ks) if k]


def _units(text):
    """识别结果切成单位：一个汉字一个单位，一个英文词一个单位。返回 [(文字, 起, 止)]（位置在 text 里）。"""
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[一-鿿]|[A-Z']+", text)]


class Matcher:
    """名字 + 别名（录入时听到的写法）。find(text) 返回 (名字结束位置, 前面的话, 后面的话) 或 None。"""

    def __init__(self, name, hint="", aliases=(), min_sim=0.8):
        self.name = name
        self.hint = hint
        self.keys = name_keys(name, hint)
        self.min_sim = min_sim
        self.aliases = {self._norm(a) for a in aliases if self._norm(a)}
        self.name_up = name.upper()
        self._cache = {}

    @staticmethod
    def _norm(s):
        return re.sub(r"[\s']", "", _PUNCT.sub(" ", (s or "").upper()))

    def _span_score(self, span_units):
        txt = "".join(u for u, _, _ in span_units)
        if txt in self._cache:
            return self._cache[txt]
        if self._norm(txt) in self.aliases or txt == self.name_up:
            r = 1.0
        else:
            cands = [""]
            for u, _, _ in span_units:
                ks = [key_of_cjk(u)] if CJK.match(u) else keys_of_word(u)
                cands = [c + k for c in cands for k in ks][:9]
            best = max((similarity(c, k) for c in cands for k in self.keys), default=0.0)
            short = min((len(k) for k in self.keys), default=0) < 4
            need = self.min_sim + (0.08 if short else 0.0)
            # 词典里本来就有的常用英文词（WHISPER、WATER）：要几乎一样才算，免得聊天撞上
            lex = lexicon()
            all_latin = not any(CJK.match(u) for u, _, _ in span_units)
            if all_latin and any(u in lex and u != self.name_up for u, _, _ in span_units):
                need = max(need, 0.97)
            r = best if best >= need else 0.0
        self._cache[txt] = r
        return r

    def find(self, text):
        t = _PUNCT.sub(" ", (text or "").upper())
        us = _units(t)
        best = None
        for i in range(len(us)):
            before = t[:us[i][1]].strip()
            cjk = len(CJK.findall(before))
            words = [w for w in re.split(r"\s+", CJK.sub(" ", before)) if w]
            if cjk > _GREET_MAX_CJK or len(words) > _GREET_MAX_WORDS or cjk + len(words) > _GREET_MAX_CJK:
                break   # 名字前面只能是很短的招呼（你好 / 早上好 / Hey / Good morning）
            if us[i][0] in _GREET_UNITS and not self.name_up.startswith(us[i][0]):
                continue  # 名字不会从「好」「HEY」开始（不然「你好WSPER」会把「好」也算进名字）
            for L in (1, 2, 3, 4):
                span = us[i:i + L]
                if len(span) < L:
                    break
                if any(not CJK.match(u) for u, _, _ in span) and L > 2:
                    break
                s = self._span_score(span)
                # 分高的优先；一样高时要短的（单位少的）
                if s and (best is None or (s, -L) > (best[0], -best[4])):
                    best = (s, span[-1][2], before, t[span[-1][2]:].strip(), L)
        if not best:
            return None
        return best[1], best[2], best[3]


_GREET_UNITS = set("你好您嘿嗨哈喽喂早上晚下午在吗醒安") | {"HEY", "HI", "HELLO", "OK", "OKAY", "YO", "GOOD", "MORNING",
                                                     "AFTERNOON", "EVENING", "WAKE", "UP", "THERE"}


# ---------------------------------------------------------------- 别名（录入/体检时 SenseVoice 认出的写法）
def aliases_file():
    return P.HOME / "name_aliases.json"


def load_aliases(name):
    try:
        d = json.loads(aliases_file().read_text(encoding="utf-8"))
        return [a for a in d.get(name, []) if isinstance(a, str)][:40]
    except Exception:  # noqa: BLE001
        return []


def save_aliases(name, new):
    """记下识别器对这个名字的写法（只留名字那一段，最多 40 个）。"""
    try:
        d = json.loads(aliases_file().read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except Exception:  # noqa: BLE001
        d = {}
    cur = [a for a in d.get(name, []) if isinstance(a, str)]
    for a in new:
        a = Matcher._norm(a)[:16]
        if a and a not in cur:
            cur.append(a)
    d = {name: cur[-40:]}          # 只留当前名字的，改名后旧的没用
    try:
        aliases_file().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return cur[-40:]

"""Reasons to NOT treat a harvested string as an identity: format keys, software/vendor names, materials, units, standards,
license strings, default placeholders, part/revision codes, plain words. reason(s, kind) -> rule name or None."""
import re
from .discover import english

def _w(s): return set(s.split())

VENDOR = _w('''solidworks autodesk inventor fusion freecad onshape siemens nx dassault catia creo ptc rhino rhinoceros blender kicad sketchup
parasolid acis spatial cascade opencascade occt adobe acrobat ghostscript inkscape libreoffice openoffice microsoft windows office word excel
powerpoint apache poi openscad unix linux ubuntu debian macos apple google mathworks matlab ansys abaqus solidedge solid edge cadence altium eagle
fritzing librecad draftsight autocad zwcad zw3d bricscad nanocad cadquery trimesh meshlab cura slic3r prusaslicer simplify3d github gitlab
docusign pdfcreator pdflatex latex tex xelatex ghostscript cairo skia chrome firefox safari mozilla stepper steptools step tools translator
unigraphics i-deas ideas pro/engineer wildfire intergraph bentley microstation rhino3d grasshopper keyshot vray maxwell cimatron mastercam
gibbscam fusion360 tinkercad shapr3d moi3d zbrush maya 3dsmax cinema4d opencv numpy python java javascript''')
UNITS = _w('''pascal newton joule watt volt ampere celsius fahrenheit meter metre millimeter millimetre centimeter inch inches
psi mpa gpa kpa rpm gram grams pound pounds ounce hertz ohm radian''')
MATERIAL = _w('''aluminum aluminium steel stainless brass copper bronze titanium plastic abs pla petg nylon wood plywood rubber glass iron zinc nickel
chrome chromium finish anodized anodised polished coating painted galvanized plate sheet bar tube rod profile extrusion casting forged
carbon fiber fibre acrylic delrin pom peek ptfe teflon silicone foam mdf pvc hdpe polycarbonate resin tpu cast''')
TERMS = _w('''dissenyat quant descripción descripcion documento archivo dxf dwg vistas cortes comprobado comprobat aprobado revisado dibujado dibuixat fecha escala versió versión format full data peça pieza mides medidas contacto zeichnung blatt werkstoff lehrzwecke datei gepr geprüft bohrung maßstab gezeichnet datum benennung material toleranz acció rugosidad espesor chapa plantilles disseny interna mètode procés enginyer connecteur coberta serveix
projection angle scale tolerance revision assembly drawing section view detail bom material quantity weight surface roughness
rev mass kg detail dimensional dimension tolerances uni iso din en jis astm asme ansi unless general standard dimensions unless otherwise specified third first title drawn checked approved date sheet page part number name size
license licence copyright creative commons gpl lgpl mit bsd cc by nd nc sa ohl ohlv2 under open hardware ohw version unknown none
default administrator user users guest owner anonymous public all rights reserved top bottom front back left right side case''')
STANDARDS = re.compile(r'^(?:iso|din|ansi|astm|asme|jis|gb|en|bs|ieee|nema|ul|csa|ce|rohs|ip)[ -_]?\d*[a-z]?$|^nema\s?\d+|^m\d+(?:x[\d.]+)?$', re.I)
PLACES = _w('''brasil brazil france germany deutschland italy italia spain espana china japan india russia canada mexico australia sweden norway
denmark finland poland netherlands belgium switzerland austria portugal greece turkey ireland scotland england wales usa america europe asia
africa london paris berlin madrid rome tokyo beijing shanghai moscow hamburg munich zurich vienna amsterdam boston chicago''')
BAD = re.compile(r'^/|[<>{}\\]|^\W*$|^(?:true|false|null|none)$|\.(?:xml|pdf|png|jpg|dll|exe)$', re.I)
CODE = re.compile(r'^[A-Za-z]{0,3}[-_ ]?\d[\d-]*[A-Za-z]?$|^[A-Za-z]{1,3}\d{3,}$|^\d[\d -]*$')
SOFT_DOMAINS = _w('microsoft.com adobe.com autodesk.com solidworks.com dassault.com w3.org apache.org gnu.org opensource.org creativecommons.org python.org')


MONTH = re.compile(r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d|\b(?:19|20)\d\d\b', re.I)


def tokens(s): return re.findall(r"[\w/+]+", s.lower())


def reason(s, kind=''):
    k = s.strip(); low = k.lower()
    if BAD.search(k): return 'format-key/symbol'
    if kind in ('email', 'phone'): return None
    if kind == 'domain':
        return 'vendor-domain' if any(low == d or low.endswith('.' + d) for d in SOFT_DOMAINS) else None
    toks = tokens(k)
    if MONTH.search(k) or len(toks) > 6: return 'date/sentence text'
    if not toks: return 'empty'
    if len(k.replace(' ', '')) <= 4 and k.isascii(): return 'short(<=4)'
    if CODE.match(k) or sum(c.isdigit() for c in k) * 2 >= len(k): return 'part/revision code'
    if STANDARDS.match(k): return 'standard'
    if any(t in VENDOR for t in toks) or low.replace(' ', '') in VENDOR: return 'software/vendor'
    if any(t in UNITS for t in toks): return 'unit'
    if any(t in MATERIAL for t in toks): return 'material/finish'
    if any(t in TERMS for t in toks) and not re.search(r'@', k): return 'drawing/license term'
    if low in PLACES: return 'place'
    if all(t in english() for t in toks): return 'dictionary words'
    return None

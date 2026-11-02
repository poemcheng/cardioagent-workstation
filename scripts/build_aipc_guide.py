"""Optional documentation build (ReportLab + fontTools); not a runtime dependency."""
from pathlib import Path
import re
from xml.sax.saxutils import escape

from fontTools.ttLib import TTFont as SourceFont
from fontTools import subset
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.cu2quPen import Cu2QuPen
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'output/pdf/CardioAgent_AMD_AIPC_Guide_v2.2_20261103.pdf'
TMP=ROOT.parent/'tmp/pdfs';TMP.mkdir(parents=True,exist_ok=True)
text=(ROOT/'docs/AMD_AIPC_GUIDE.md').read_text(encoding='utf-8')

# Subset installed Noto CJK TC and convert cubic outlines for embedded TrueType.
font_path=TMP/'CardioGuide.ttf'
font=SourceFont('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',fontNumber=3)
options=subset.Options();options.notdef_outline=True
subsetter=subset.Subsetter(options=options);subsetter.populate(text=text+'CardioAgent AMD AI PC 2.2 0123456789 / | 研究展示 操作與驗收指南')
subsetter.subset(font);glyphset=font.getGlyphSet();order=font.getGlyphOrder();glyphs={}
for name in order:
    pen=TTGlyphPen(None);glyphset[name].draw(Cu2QuPen(pen,1.0,reverse_direction=True));glyphs[name]=pen.glyph()
builder=FontBuilder(font['head'].unitsPerEm,isTTF=True)
builder.setupGlyphOrder(order);builder.setupCharacterMap(font.getBestCmap());builder.setupGlyf(glyphs)
builder.setupHorizontalMetrics({name:font['hmtx'].metrics[name] for name in order})
builder.setupHorizontalHeader(ascent=font['hhea'].ascent,descent=font['hhea'].descent)
builder.setupNameTable({'familyName':'CardioGuide','styleName':'Regular','uniqueFontIdentifier':'CardioGuide2.2','fullName':'CardioGuide Regular','psName':'CardioGuide-Regular'})
builder.setupOS2(sTypoAscender=font['hhea'].ascent,sTypoDescender=font['hhea'].descent,usWinAscent=font['hhea'].ascent,usWinDescent=abs(font['hhea'].descent))
builder.setupPost();builder.setupMaxp();builder.save(font_path)
pdfmetrics.registerFont(TTFont('Guide',str(font_path)))
pdfmetrics.registerFontFamily('Guide',normal='Guide',bold='Guide',italic='Guide',boldItalic='Guide')
ink=colors.HexColor('#20303d');muted=colors.HexColor('#526371');line=colors.HexColor('#d5dce1')
body=ParagraphStyle('body',fontName='Guide',fontSize=9.5,leading=15.5,textColor=ink,spaceAfter=8,wordWrap='CJK',splitLongWords=True)
small=ParagraphStyle('small',parent=body,fontSize=8,leading=12,spaceAfter=3)
heading=ParagraphStyle('heading',parent=body,fontSize=14,leading=21,spaceBefore=15,spaceAfter=10,keepWithNext=True)
title=ParagraphStyle('title',parent=body,fontSize=24,leading=32,spaceAfter=18)
code=ParagraphStyle('code',parent=body,fontSize=8,leading=13,backColor=colors.HexColor('#f0f3f5'),borderPadding=8,spaceBefore=7,spaceAfter=12)


def markup(s):
    s=escape(s)
    s=re.sub(r'`([^`]+)`',lambda m:'<font color="#1a5669">'+m.group(1)+'</font>',s)
    return s


story=[Paragraph('CardioAgent<br/>AMD AI PC 操作與驗收指南',title),Paragraph('LOCAL CLINICAL WORKFLOW DEMO  |  v2.2  |  2026-11-03',small),Spacer(1,12)]
lines=text.splitlines();i=1
while i<len(lines):
    s=lines[i]
    if not s.strip():i+=1;continue
    if s.startswith('## '):story.append(Paragraph(markup(s[3:]),heading));i+=1;continue
    if s.startswith('```'):
        block=[];i+=1
        while i<len(lines) and not lines[i].startswith('```'):block.append(lines[i]);i+=1
        story.append(Paragraph('<br/>'.join(escape(x) for x in block),code));i+=1;continue
    if s.startswith('|'):
        rows=[]
        while i<len(lines) and lines[i].startswith('|'):
            if not re.match(r'^\|[-:| ]+\|$',lines[i]):rows.append([Paragraph(markup(x.strip()),small) for x in lines[i].strip('|').split('|')])
            i+=1
        n=len(rows[0]);widths=([84,190,225] if n==3 else [499/n]*n)
        table=Table(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7eef2')),('BACKGROUND',(0,1),(-1,-1),colors.HexColor('#fafbfc')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,0),.7,line),('LINEBELOW',(0,1),(-1,-1),.3,line)]))
        story.extend([table,Spacer(1,10)]);continue
    para=[s];i+=1
    while i<len(lines) and lines[i].strip() and not lines[i].startswith(('## ','|','```')):
        if re.match(r'^\d+\. ',lines[i]):break
        para.append(lines[i]);i+=1
    story.append(Paragraph(markup(' '.join(para)),body))


def frame(canvas,doc):
    canvas.saveState();w,h=A4
    canvas.setStrokeColor(line);canvas.setLineWidth(.5);canvas.line(48,h-38,w-48,h-38)
    canvas.setFont('Guide',8);canvas.setFillColor(muted)
    canvas.drawString(48,h-29,'CARDIOAGENT / AMD AI PC 2.2')
    canvas.drawString(48,27,'研究展示 · 軟體流程驗證與目標機採證分開記錄')
    canvas.drawRightString(w-48,27,str(doc.page));canvas.restoreState()


OUT.parent.mkdir(parents=True,exist_ok=True)
SimpleDocTemplate(str(OUT),pagesize=A4,rightMargin=48,leftMargin=48,topMargin=54,bottomMargin=48,title='CardioAgent AMD AI PC 操作與驗收指南',author='CardioAgent project').build(story,onFirstPage=frame,onLaterPages=frame)
print(OUT)

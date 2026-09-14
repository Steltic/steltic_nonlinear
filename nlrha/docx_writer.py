"""docx_writer.py -- a dependency-free Word (.docx) writer for the 16.1.4 design criteria document.

Headings, paragraphs, bullet lists and tables, written as WordprocessingML in a zip. No python-docx needed in
the module environment; Word, LibreOffice and Google Docs open the result and reviewers can mark it up.

    d = Doc(title="...", subtitle="...")
    d.heading("1. Scope", 1); d.para("text"); d.bullet("item"); d.table([["a", "b"], ["1", "2"]], header=True)
    d.save("file.docx")
"""
from __future__ import annotations
import datetime, zipfile
from xml.sax.saxutils import escape

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
</Relationships>"""
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles %s>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="21"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:sz w:val="22"/><w:color w:val="595959"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="25"/><w:color w:val="2F5496"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr><w:spacing w:after="40"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:basedOn w:val="Normal"/><w:rPr><w:i/><w:sz w:val="19"/><w:color w:val="595959"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="17"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="Grid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:color="BFBFBF"/><w:left w:val="single" w:sz="4" w:color="BFBFBF"/><w:bottom w:val="single" w:sz="4" w:color="BFBFBF"/><w:right w:val="single" w:sz="4" w:color="BFBFBF"/>
<w:insideH w:val="single" w:sz="4" w:color="BFBFBF"/><w:insideV w:val="single" w:sz="4" w:color="BFBFBF"/></w:tblBorders><w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>""" % _W
_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering %s>
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="540" w:hanging="270"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr></w:lvl>
</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>""" % _W


def _run(text, bold=False, italic=False, size=None):
    rpr = ""
    if bold or italic or size:
        rpr = "<w:rPr>%s%s%s</w:rPr>" % ("<w:b/>" if bold else "", "<w:i/>" if italic else "", ('<w:sz w:val="%d"/>' % size) if size else "")
    parts = str(text).split("\n")
    out = []
    for i, p in enumerate(parts):
        if i:
            out.append("<w:r>%s<w:br/></w:r>" % rpr)
        out.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, escape(p)))
    return "".join(out)


class Doc:
    def __init__(self, title="", subtitle="", author="Steltic_nonlinear", footer=""):
        self.body = []; self.title = title; self.author = author; self.footer = footer or title
        if title:
            self.body.append('<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>%s</w:p>' % _run(title))
        if subtitle:
            self.body.append('<w:p><w:pPr><w:pStyle w:val="Subtitle"/></w:pPr>%s</w:p>' % _run(subtitle))

    def heading(self, text, level=1):
        self.body.append('<w:p><w:pPr><w:pStyle w:val="Heading%d"/></w:pPr>%s</w:p>' % (max(1, min(3, level)), _run(text)))

    def para(self, text, bold=False, italic=False, style=None):
        st = ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else ""
        self.body.append("<w:p>%s%s</w:p>" % (st, _run(text, bold, italic)))

    def note(self, text):
        self.para(text, style="Note")

    def code(self, text):
        for line in str(text).splitlines() or [""]:
            self.para(line, style="Code")

    def bullet(self, text, bold_lead=None):
        lead = _run(bold_lead + " ", bold=True) if bold_lead else ""
        self.body.append('<w:p><w:pPr><w:pStyle w:val="ListBullet"/></w:pPr>%s%s</w:p>' % (lead, _run(text)))

    def table(self, rows, header=True, widths=None):
        if not rows:
            return
        ncol = max(len(r) for r in rows)
        widths = widths or [int(9000 / ncol)] * ncol
        x = ['<w:tbl><w:tblPr><w:tblStyle w:val="Grid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid>%s</w:tblGrid>' % "".join('<w:gridCol w:w="%d"/>' % w for w in widths)]
        for i, r in enumerate(rows):
            x.append("<w:tr>")
            for j in range(ncol):
                cell = r[j] if j < len(r) else ""
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="E7EDF6"/>' if (header and i == 0) else ""
                x.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr><w:p><w:pPr><w:spacing w:after="0"/></w:pPr>%s</w:p></w:tc>'
                         % (widths[j], shade, _run(cell, bold=(header and i == 0), size=18)))
            x.append("</w:tr>")
        x.append("</w:tbl>")
        self.body.append("".join(x)); self.body.append("<w:p/>")

    def page_break(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def save(self, path):
        doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document %s><w:body>%s'
               '<w:sectPr><w:footerReference w:type="default" r:id="rId3"/><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1300" w:right="1250" w:bottom="1300" w:left="1250" w:header="700" w:footer="700"/></w:sectPr>'
               '</w:body></w:document>' % (_W, "".join(self.body)))
        footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr %s><w:p><w:pPr><w:jc w:val="right"/></w:pPr>%s'
                  '<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:t xml:space="preserve"> · page </w:t></w:r><w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
                  '<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>'
                  % (_W, _run(self.footer, size=16)))
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
                % (escape(self.title), escape(self.author), now, now))
        app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
               '<Application>Steltic_nonlinear</Application></Properties>')
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _CT); z.writestr("_rels/.rels", _RELS); z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
            z.writestr("word/document.xml", doc); z.writestr("word/styles.xml", _STYLES); z.writestr("word/numbering.xml", _NUMBERING)
            z.writestr("word/footer1.xml", footer); z.writestr("docProps/core.xml", core); z.writestr("docProps/app.xml", app)
        return path

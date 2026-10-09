from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import re
from xml.etree import ElementTree as ET

class UserError(Exception): pass


def filename(name):
    name=Path(str(name).replace('\\','/')).name
    return re.sub(r'[^\w. ()-]','_',name)[:150] or 'exam.docx'


def validate_docx(data, name, limit):
    if not str(name).lower().endswith('.docx'): raise UserError('Please send a Word .docx file. PDF and images are not supported.')
    if not data or len(data)>limit: raise UserError('The file is empty or exceeds the upload limit.')
    try:
        with ZipFile(BytesIO(data)) as z:
            entries=z.infolist()
            if len(entries)>5000 or sum(i.file_size for i in entries)>100*1024*1024:
                raise UserError('This Word file is too large when unpacked.')
            names={i.filename for i in entries}
            if not {'[Content_Types].xml','word/document.xml'}.issubset(names): raise UserError('This is not a valid Word .docx document.')
            if any('vbaproject' in n.lower() for n in names): raise UserError('Macro-enabled documents are not supported.')
            if z.testzip(): raise UserError('This Word file is damaged. Open it in Word and save it again as .docx.')
            content_types=ET.fromstring(z.read('[Content_Types].xml'))
            expected='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
            if not any(x.attrib.get('PartName')=='/word/document.xml' and x.attrib.get('ContentType')==expected for x in content_types):
                raise UserError('Please save this document in standard Word .docx format.')
            root=ET.fromstring(z.read('word/document.xml'))
            if root.tag!='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document': raise UserError('This Word file is damaged.')
    except (BadZipFile,ET.ParseError,KeyError,RuntimeError,NotImplementedError,ValueError):
        raise UserError('This Word file is damaged. Open it in Word and save it again as .docx.') from None

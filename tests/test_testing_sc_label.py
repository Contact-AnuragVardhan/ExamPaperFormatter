"""Display name changed without changing existing API identifiers."""
from pathlib import Path

from service.whatsapp import WhatsAppFlow
from service.menus import replies


def test_new_whatsapp_command_and_legacy_alias(env):
    svc, _, _ = env
    flow=WhatsAppFlow(svc)
    menu=flow.main('15551230000')
    assert any(row['id']=='main.spellcheck' and row['title']=='TESTING SC' for row in replies(menu))
    flow.handle('15551230000',{'type':'text','text':{'body':'TESTING SC'}},'rename1')
    assert svc.db.session('15551230000',30)['state']=='spellcheck_upload'
    flow.handle('15551230000',{'type':'text','text':{'body':'Hi'}},'rename2')
    flow.handle('15551230000',{'type':'text','text':{'body':'SPELLCHECK'}},'rename3')
    assert svc.db.session('15551230000',30)['state']=='spellcheck_upload'


def test_web_display_label_without_api_identifier_change():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web'/'index.html').read_text(encoding='utf-8')
    js=(root/'web'/'app.js').read_text(encoding='utf-8')
    assert '<strong>TESTING SC</strong>' in html
    assert 'value="spellcheck"' in html
    assert "api('/api/spellcheck'" in js

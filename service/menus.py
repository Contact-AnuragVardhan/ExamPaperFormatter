"""Native WhatsApp menu payloads within Meta's interactive limits."""
FOOTER='Say Hi anytime to return to the main menu.'

def menu(title,body,choices,button='Open Menu',force_list=False):
    if not choices or len(choices)>10: raise ValueError('A menu needs one to ten choices.')
    result={'type':'list' if force_list or len(choices)>3 else 'button',
            'header':{'type':'text','text':title[:60]},'body':{'text':body[:1024]},'footer':{'text':FOOTER}}
    if result['type']=='button':
        result['action']={'buttons':[{'type':'reply','reply':{'id':c[0],'title':c[1][:20]}} for c in choices]}
    else:
        rows=[]
        for c in choices:
            row={'id':c[0],'title':c[1][:24]}
            if len(c)>2 and c[2]: row['description']=c[2][:72]
            rows.append(row)
        result['action']={'button':button[:20],'sections':[{'title':'Choose an option','rows':rows}]}
    return result

def replies(payload):
    if payload['type']=='button': return [item['reply'] for item in payload['action']['buttons']]
    return [row for section in payload['action']['sections'] for row in section['rows']]

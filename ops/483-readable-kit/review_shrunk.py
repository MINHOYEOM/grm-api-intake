import json,sys;sys.stdout.reconfigure(encoding='utf-8')
r=json.load(open(f'report-{sys.argv[1]}.json',encoding='utf-8'))
for x in r:
  if x['flags'] and all(f.startswith('shrunk') for f in x['flags']):
    print('##',x['finding_id'][-8:],x['ratio'],'en',len(x['en']),'new',len(x['new']))
    print(' EN_END:',x['en'][-170:].replace('\n',' '))
    print(' NEW_END:',x['new'][-110:].replace('\n',' '))

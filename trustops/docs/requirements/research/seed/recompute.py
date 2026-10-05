import json,datetime,statistics
d=json.load(open('/private/tmp/claude-502/-Users-apahuja-Downloads-Hackathon/65165fc9-1a2d-4120-8b04-49b14b887f3b/scratchpad/v2/seed.clean.json'))
SM=d['scoring_model']; W={c['id']:c['weight'] for c in d['controls']}; DW=SM['domain_weights']
def band(x):
    for b in SM['bands']:
        if b['min']<=x<=b['max']: return b['band']
    return None
def score(inputs, rounded_residual=False):
    areas={}; tot=sum(DW[a] for a in inputs)
    for a,v in inputs.items():
        cs=[c for c in v['controls'] if not c.get('planned')]
        ws=sum(W[c['id']] for c in cs)
        ce=sum(W[c['id']]/ws*c['rating'] for c in cs) if cs else 0
        ret=max(SM['retention_floor'],1-SM['retention_factor']*ce/10)
        inh=v['L']*v['I']; res=inh*ret; w=DW[a]/tot
        areas[a]=dict(inherent=inh,ce=ce,ret=ret,res=res,w=w,contrib=w*res)
    comp=sum(x['contrib'] for x in areas.values())
    return areas,comp
# demo
D=d['demo']
for k in ('score_before','score_after'):
    inp={a:{'L':v['L'],'I':v['I'],'controls':v['controls']} for a,v in D[k]['areas'].items()}
    areas,comp=score(inp)
    print(k)
    for a,x in areas.items():
        s=D[k]['areas'][a]
        print(f"  {a}: inh {x['inherent']} ce {x['ce']:.4f} (seed {s['ce']}) ret {x['ret']:.4f} (seed {s['retention']}) res {x['res']:.4f} (seed {s['residual']}) w {x['w']:.4f} (seed {s['weight']}) contrib {x['contrib']:.4f} (seed {s['contribution']})")
    print(f"  composite raw {comp:.4f} -> {round(comp,1)} band {band(round(comp,1))}; seed {D[k]['composite']} band {D[k]['band']}")
    # alt: from rounded residuals
    alt=sum(round(x['res'],1)*x['w'] for x in areas.values()); print(f"  alt (rounded residuals) {alt:.4f}")
    # boundary distances
    c=round(comp,1); print('  dist to 10.1:',round(abs(10.1-c),2),' dist to 10.0:',round(abs(10.0-c),2))
# all cases
bad=[];bd={}
for c in d['cases']:
    areas,comp=score(c['inputs']); r=round(comp+1e-9,1)
    sc=c['score']; b=band(r); bd[b]=bd.get(b,0)+1
    keys=list(sc.keys())
    if abs(r-sc['composite'])>0.05 or b!=sc['band']: bad.append((c['id'],r,sc['composite'],b,sc['band']))
print('score keys example',[k for k in d['cases'][0]['score'].keys()])
print('cases',len(d['cases']),'mismatches',bad)
print('band dist recomputed',sorted(bd.items()),'seed',d['derived']['band_distribution'])
# boundary flags
bf=[c for c in d['cases'] if c['score'].get('boundary_flag')]
print('boundary flagged cases',len(bf))

"""Match existing local scenery images using NexusArk-compatible filename conventions."""
from datetime import datetime
from pathlib import PurePosixPath

SEASONS=('spring','summer','autumn','winter')
EXTENDED={'early_spring':('spring','winter','autumn','summer'),'early_summer':('summer','spring','autumn','winter'),
          'late_summer':('summer','autumn','spring','winter'),'late_autumn':('autumn','winter','summer','spring')}
TIMES={'early_morning':('morning','daytime'),'late_morning':('morning','daytime'),
       'afternoon':('noon','late_morning','morning','daytime'),'noon':('late_morning','morning','daytime'),
       'evening':('night',),'midnight':('night',),'morning':('daytime',),'night':(),'daytime':()}


def time_context(config,now=None):
    now=now or datetime.now()
    season='spring' if now.month in (3,4,5) else 'summer' if now.month in (6,7,8) else 'autumn' if now.month in (9,10,11) else 'winter'
    hour=now.hour
    period='early_morning' if 4<=hour<6 else 'morning' if 6<=hour<10 else 'late_morning' if 10<=hour<12 else 'afternoon' if 12<=hour<16 else 'evening' if 16<=hour<19 else 'night' if 19<=hour<23 else 'midnight'
    settings=config.get('time_settings',{})
    if not isinstance(settings,dict):settings={}
    if not settings:
        overrides=config.get('override_settings',{})
        settings=overrides.get('time_settings',{}) if isinstance(overrides,dict) else {}
    if isinstance(settings,dict) and settings.get('mode')=='fixed':
        configured=settings.get('fixed_season')
        if isinstance(configured,str) and configured in (*SEASONS,*EXTENDED):season=configured
        configured=settings.get('fixed_time_of_day')
        if isinstance(configured,str) and configured in TIMES:period=configured
    return season,period


def find_image(images,location,season,period):
    if not location:return ''
    if season in EXTENDED:seasons=(season,*EXTENDED[season])
    elif season in SEASONS:
        index=SEASONS.index(season);seasons=tuple(SEASONS[(index-i)%4] for i in range(4))
    else:seasons=SEASONS
    periods=(period,*TIMES.get(period,()))
    bases=[f'{location}_{s}_{t}' for t in periods for s in seasons]
    bases += [f'{location}_{t}' for t in periods]
    exact=(*bases,f'{location}_{season}',location)
    # Prefer the standard scenery directory and PNG, with deterministic raster fallbacks.
    ranked=sorted(images,key=lambda path:(not path.startswith('spaces/images/'),PurePosixPath(path).suffix.lower()!='.png',path.casefold()))
    names=[(path,PurePosixPath(path).stem.casefold()) for path in ranked]
    for candidate in exact:
        match=next((path for path,stem in names if stem==candidate.casefold()),None)
        if match:return match
    for candidate in bases:
        match=next((path for path,stem in names if stem.startswith(candidate.casefold()+'_')),None)
        if match:return match
    related=[(path,stem) for path,stem in names if stem.startswith(location.casefold()+'_')]
    for path,stem in related:
        if not any('_'+period+'_' in stem+'_' for period in TIMES):return path
    return related[0][0] if related else ''

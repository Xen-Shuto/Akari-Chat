"""Link-only music recommendations; no playback or account access."""
from urllib.parse import quote
from .schema import schema
from .contracts import ToolSpec, ToolOutput, ToolFailure


def recommend_music(app, context, args):
    context.checkpoint()
    if not args['tracks']:raise ToolFailure('invalid_arguments','曲を1件以上指定してください。')
    selected=app.music_settings.view()['values']
    tracks=[]
    for item in args['tracks']:
        query=quote(' '.join([item['title'],item.get('artist','')]).strip(),safe='')
        links={
            'youtube':('YouTube','https://www.youtube.com/results?search_query='+query),
            'spotify':('Spotify','https://open.spotify.com/search/'+query),
            'bandcamp':('Bandcamp','https://bandcamp.com/search?q='+query),
            'soundcloud':('SoundCloud','https://soundcloud.com/search?q='+query)}
        tracks.append({**item,'links':{label:url for key,(label,url) in links.items() if key=='youtube' or selected[key]}})
    return ToolOutput(data={'mood':args['mood'],'reason':args['reason'],'tracks':tracks},
        message='モデルが挙げた候補への検索リンクです。実在・配信状況の検証や再生は行っていません。')

ENTRIES=[ToolSpec(schema('recommend_music','共通設定で選択されたサービスへの1〜3曲の音楽推薦リンクを作る。YouTubeは常に含む。検索実行・再生・アカウント操作は行わない。',{
 'mood':{'type':'string','maxLength':200},'reason':{'type':'string','maxLength':1000},
 'tracks':{'type':'array','maxItems':3,'items':{'type':'object','properties':{
    'title':{'type':'string','minLength':1,'maxLength':200},'artist':{'type':'string','maxLength':200},
    'reason':{'type':'string','maxLength':500}},'required':['title','artist','reason'],'additionalProperties':False}}},
 ['mood','reason','tracks']),recommend_music,category='web',operation='read')]

"""Pure finite campaign policy; economic promotion is outside this contract."""
from services.research.ai_factory import validate_config


def validate_campaign(config):
    if isinstance(config, dict) and 'market' in config:
        from services.research.gold_factory_campaign import validate_campaign as gold_campaign
        return gold_campaign(config)
    if not isinstance(config,dict) or set(config)-{'trigger','champion_attempt','research_protocol'}!={'campaign_id','objective','mode','budget','max_cycles','interval_hours','source_ids'}:
        raise ValueError('invalid campaign fields')
    if config.get('trigger','schedule') not in ('schedule','drift'):raise ValueError('invalid trigger')
    champion=config.get('champion_attempt')
    if config.get('trigger')=='drift' and (type(champion) is not int or not 1<=champion<=1000):raise ValueError('drift campaign requires frozen model')
    for name,lo,hi in (('campaign_id',1,1000),('max_cycles',1,12),('interval_hours',1,168)):
        if type(config[name]) is not int or not lo<=config[name]<=hi:raise ValueError('invalid campaign '+name)
    validate_config({'attempt':1,'mode':config['mode'],'budget':config['budget'],'objective':config['objective'],
                     **({'research_protocol':config['research_protocol']} if 'research_protocol' in config else {}),
                     'search_plan':{'version':2,'excluded_ids':[],'source_ids':config['source_ids']}})
    if config['budget']*config['max_cycles']>72:raise ValueError('campaign exceeds distinct candidate budget')
    return dict(config)

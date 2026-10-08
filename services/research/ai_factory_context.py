"""Research knowledge time and market cutoffs are separate from validation scope."""
from dataclasses import dataclass,asdict
import re
from services.research.ai_factory_store import digest

SCOPES=('INNER_SEARCH','OUTER_EXPLANATION','NATURAL_FORWARD')


def is_hash(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


@dataclass(frozen=True)
class ResearchContext:
    experiment_hash:str
    snapshot_hash:str
    partition_hash:str
    as_of_ms:int
    market_as_of_ms:int
    scope:str

    def __post_init__(self):
        if (not all(is_hash(v) for v in (self.experiment_hash,self.snapshot_hash,self.partition_hash))
                or any(type(v) is not int or v<0 for v in (self.as_of_ms,self.market_as_of_ms))
                or self.market_as_of_ms>self.as_of_ms or self.scope not in SCOPES):
            raise ValueError('invalid research time context')

    @property
    def cache_key(self):return digest(asdict(self))

    def allows(self,record):
        """Unknown provenance, another fold, future knowledge and outer feedback stay unreadable."""
        if (record.get('experiment_hash')!=self.experiment_hash or record.get('snapshot_hash')!=self.snapshot_hash
                or record.get('partition_hash')!=self.partition_hash or record.get('scope')!=self.scope):return False
        return (type(record.get('known_at_ms')) is int and 0<=record['known_at_ms']<=self.as_of_ms
                and type(record.get('market_max_ms')) is int and 0<=record['market_max_ms']<=self.market_as_of_ms)

    def document(self):return {'schema':'QUANTOS_RESEARCH_CONTEXT_V1',**asdict(self),'context_hash':self.cache_key}

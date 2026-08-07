"""确定性、分层、失败关闭的验证 DAG。生产 adapter 只调用已登记的 typed 引擎。"""
from __future__ import annotations
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib, json, math, re, statistics, time
from statistics import NormalDist
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable
from services.research.alpha_identity import AlphaDefinition, alpha_id as canonical_alpha_id, canonical_alpha_json
from services.research.alpha_family import family_id as canonical_family_id
from services.research.trial_ledger import EffectiveTrialCount, TrialLedger, TrialLedgerConflict, TrialLedgerError, TrialRecord

_STAGE_ORDER = ("cheap", "oos", "realism")
_TERMINAL = {"PASS", "BLOCK"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

def _freeze(value: Any) -> Any:
    if isinstance(value, RegisteredLeakageCompute): return value
    if callable(getattr(value, "trade_cost", None)) and callable(getattr(value, "impact_bps", None)): return value
    if hasattr(value, "family_of") and hasattr(value, "basis"): return value
    if isinstance(value, Enum): return value.value
    if hasattr(value, "model_dump") and callable(value.model_dump): return _freeze(value.model_dump())
    if is_dataclass(value): return MappingProxyType({f.name: _freeze(getattr(value,f.name)) for f in fields(value)})
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value): raise TypeError("mapping keys must be strings")
        return MappingProxyType({k:_freeze(value[k]) for k in sorted(value)})
    if isinstance(value,(tuple,list)): return tuple(_freeze(v) for v in value)
    if isinstance(value,(set,frozenset)): return tuple(sorted(_freeze(v) for v in value))
    if type(value) in (str,int,bool) or value is None: return value
    if type(value) is float and math.isfinite(value): return value
    raise TypeError(f"value is not canonical JSON: {type(value).__name__}")

def _execution_snapshot(value: Any) -> Any:
    """Deep-freeze containers and copy only exact trusted engine input types."""
    from quantos.contracts.cost import CostModel
    from quantos.contracts.data import Bar
    from quantos.contracts.validation import PboAlignment
    from services.alpha.family_testing import FamilyAssignment
    if type(value) is RegisteredLeakageCompute:
        return value
    if type(value) is PboAlignment:
        return value
    if type(value) is Bar:
        return value.model_copy(deep=True)
    if type(value) is CostModel:
        return CostModel(fee_bps=value.fee_bps, slippage_bps=value.slippage_bps, impact_coef=value.impact_coef)
    if type(value) is FamilyAssignment:
        return FamilyAssignment(
            MappingProxyType(dict(value.family_of)),
            MappingProxyType(dict(value.basis)),
        )
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TypeError("mapping keys must be strings")
        return MappingProxyType({k: _execution_snapshot(value[k]) for k in sorted(value)})
    if isinstance(value, (tuple, list)):
        return tuple(_execution_snapshot(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_execution_snapshot(v) for v in value)
    if type(value) in (str, int, bool) or value is None:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(f"unsupported execution input: {type(value).__name__}")

def _jsonable(value: Any) -> Any:
    if isinstance(value, RegisteredLeakageCompute): return {"registered_compute":value.implementation_id}
    if isinstance(value, Enum): return value.value
    if hasattr(value, "model_dump") and callable(value.model_dump): return {"__type__":f"{type(value).__module__}.{type(value).__qualname__}", **_jsonable(value.model_dump())}
    if isinstance(value, Mapping): return {k:_jsonable(value[k]) for k in sorted(value)}
    if isinstance(value,(tuple,list,set,frozenset)): return [_jsonable(v) for v in value]
    if is_dataclass(value): return {"__type__":f"{type(value).__module__}.{type(value).__qualname__}", **{f.name:_jsonable(getattr(value,f.name)) for f in fields(value)}}
    if type(value) in (str,int,bool) or value is None: return value
    if type(value) is float and math.isfinite(value): return value
    raise TypeError(f"value is not canonical JSON: {type(value).__name__}")

def _canonical(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)

def _digest(value: Any) -> str: return hashlib.sha256(_canonical(value).encode()).hexdigest()


_LEAKAGE_REGISTRY: dict[str, tuple[Any, str | None]] = {}

@dataclass(frozen=True, slots=True)
class RegisteredLeakageCompute:
    implementation_id: str
    compute: Any
    exact_definition_id: str | None = None
    def __post_init__(self):
        if type(self.implementation_id) is not str or not self.implementation_id or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", self.implementation_id): raise ValueError("invalid leakage implementation identity")
        if not callable(self.compute) or getattr(self.compute, "__name__", "") == "<lambda>": raise ValueError("registered named leakage compute required")
        if self.exact_definition_id is not None and (type(self.exact_definition_id) is not str or not _HEX64.fullmatch(self.exact_definition_id)): raise ValueError("invalid leakage definition identity")
        if _LEAKAGE_REGISTRY.get(self.implementation_id) != (self.compute,self.exact_definition_id): raise ValueError("compute must be registered by trusted constructor")

def register_leakage_compute(implementation_id: str, compute: Any, *, exact_definition_id: str | None = None) -> RegisteredLeakageCompute:
    if type(implementation_id) is not str or not implementation_id or not callable(compute) or getattr(compute, "__name__", "") == "<lambda>": raise ValueError("named leakage compute required")
    registration=(compute,exact_definition_id)
    prior=_LEAKAGE_REGISTRY.get(implementation_id)
    if prior is not None and prior != registration: raise ValueError("leakage implementation identity collision")
    _LEAKAGE_REGISTRY[implementation_id]=registration
    return RegisteredLeakageCompute(implementation_id,compute,exact_definition_id)

def _panel_digest(panel: Any) -> str:
    def scrub(v):
        if isinstance(v, RegisteredLeakageCompute): return {"registered_compute":v.implementation_id,"exact_definition_id":v.exact_definition_id}
        if isinstance(v, Mapping): return {k:scrub(v[k]) for k in v}
        if isinstance(v,(tuple,list)): return [scrub(x) for x in v]
        if callable(v): raise TypeError("unregistered callable in panel")
        return v
    return _digest(scrub(panel))


def _finite_tree(value: Any, *, allow_none: bool=False) -> bool:
    if value is None: return allow_none
    if type(value) in (int,float): return math.isfinite(float(value))
    if isinstance(value, Mapping): return all(_finite_tree(v,allow_none=allow_none) for v in value.values())
    if isinstance(value,(tuple,list)): return all(_finite_tree(v,allow_none=allow_none) for v in value)
    return isinstance(value,str) or isinstance(value,bool)

def _strict_result(value: Any, required: tuple[str,...]) -> Mapping[str,Any]:
    if not isinstance(value, Mapping) or any(k not in value for k in required): raise ValueError("malformed engine result")
    return value

@dataclass(frozen=True, slots=True)
class StageEvidence:
    stage: str; version: str; outcome: str; evidence: Mapping[str,Any] = field(default_factory=dict); reason: str|None = None; implementation_id: str = ""
    def __post_init__(self):
        if self.stage not in _STAGE_ORDER or type(self.version) is not str or not self.version: raise ValueError("stage and version are required")
        if self.outcome not in _TERMINAL: raise ValueError("stage outcome must be PASS or BLOCK")
        if type(self.implementation_id) is not str or not self.implementation_id: raise ValueError("implementation identity is required")
        frozen=_freeze(self.evidence)
        if not isinstance(frozen,Mapping) or not frozen: raise ValueError("stage evidence is required")
        if self.reason is not None and type(self.reason) is not str: raise TypeError("reason must be string or None")
        object.__setattr__(self,"evidence",frozen)
        if self.outcome == "BLOCK" and self.reason is None: object.__setattr__(self,"reason","stage blocked")
    @property
    def evidence_hash(self): return _digest({"stage":self.stage,"version":self.version,"implementation_id":self.implementation_id,"outcome":self.outcome,"evidence":self.evidence,"reason":self.reason})

@dataclass(frozen=True, slots=True)
class ValidationContext:
    candidate: Any; panel: Any; policy: "ValidationPolicy"; trial_id: str; panel_digest: str; policy_digest: str; event_at: int; effective_trial_count: int|None = None; effective_trial_ids: tuple[str,...] = (); trial_set_digest: str = ""; effective_definition_ids: tuple[str,...] = ()
    @property
    def execution_id(self): return _digest({"trial_id":self.trial_id,"stage":"validation","attempt_independent":True})

@runtime_checkable
class ValidationStage(Protocol):
    name: str; version: str; implementation_id: str
    def evaluate(self, context: ValidationContext) -> StageEvidence: ...

class _TypedAdapter:
    name: str; version = "1"
    implementation_id: str
    def __init__(self, implementation_id: str|None=None):
        canonical=type(self).implementation_id
        if implementation_id is not None and implementation_id != canonical:
            raise ValueError("canonical production implementation identity cannot be overridden")
        self.implementation_id=canonical
    def _block(self, reason: str, **detail): return StageEvidence(self.name,self.version,"BLOCK",{"adapter":self.implementation_id,**detail},reason,self.implementation_id)
    def _pass(self, **detail): return StageEvidence(self.name,self.version,"PASS",{"adapter":self.implementation_id,**detail},None,self.implementation_id)

class CheapValidationAdapter(_TypedAdapter):
    name="cheap"; implementation_id="services.features.leakage.detect_leakage:1"
    def evaluate(self, context):
        from services.features.leakage import detect_leakage
        x=context.panel.get("cheap") if isinstance(context.panel,Mapping) else None
        required=("feature_id","compute","bars","target_event_time")
        if not isinstance(x,Mapping) or any(k not in x for k in required): return self._block("missing leakage inputs", missing_input=list(required))
        compute=x["compute"]
        if not isinstance(compute,RegisteredLeakageCompute): return self._block("unregistered leakage compute")
        candidate_definition=getattr(context.candidate,"definition",None)
        if isinstance(candidate_definition,AlphaDefinition):
            candidate_exact=_digest({"canonical_definition":canonical_alpha_json(candidate_definition)})
        else:
            candidate_exact=getattr(context.candidate,"exact_definition_id",None)
        if candidate_exact is not None and compute.exact_definition_id != candidate_exact:
            return self._block("leakage compute definition mismatch")
        try:
            bars=tuple(x["bars"])
            target_event_time=x["target_event_time"]
            if type(target_event_time) is not int or target_event_time > context.event_at:
                return self._block("leakage inputs exceed validation as-of")
            if not bars or any(
                type(getattr(bar,"event_time",None)) is not int
                or type(getattr(bar,"knowledge_time",None)) is not int
                or bar.event_time > context.event_at
                or bar.knowledge_time > context.event_at
                for bar in bars
            ):
                return self._block("leakage inputs exceed validation as-of")
            from quantos.contracts.feature import FeatureValue
            baseline=compute.compute(bars)
            if not isinstance(baseline,(tuple,list)) or not baseline or any(not isinstance(value,FeatureValue) for value in baseline):
                return self._block("missing target feature evidence")
            target_values=tuple(
                value for value in baseline
                if value.feature_id == x["feature_id"]
                and value.event_time == x["target_event_time"]
                and (x.get("instrument_id") is None or value.instrument_id == x["instrument_id"])
            )
            if not target_values:
                return self._block("missing target feature evidence")
            result=detect_leakage(x["feature_id"],compute.compute,x["bars"],x["target_event_time"],x.get("instrument_id"))
        except Exception as exc: return self._block("leakage engine unavailable", exception_type=type(exc).__name__)
        if not hasattr(result,"passed") or type(result.passed) is not bool: return self._block("malformed leakage result")
        return self._pass(leakage_passed=result.passed, detail=getattr(result,"detail","")) if result.passed else self._block("leakage gate blocked", leakage_passed=False)

class OOSValidationAdapter(_TypedAdapter):
    name="oos"; implementation_id="services.validation.walk_forward;services.validation.deflated_sharpe;services.alpha.multiple_testing:1"
    def evaluate(self, context):
        from services.validation.walk_forward import purged_kfold, walk_forward_splits
        from services.validation.catalog_evidence import create_catalog_evidence, verify_catalog_evidence
        from services.validation.pbo_evidence import create_batch_pbo_evidence, verify_batch_pbo_evidence
        from services.alpha.multiple_testing import benjamini_hochberg, benjamini_hochberg_yekutieli
        from services.alpha.family_testing import hierarchical_fdr
        x=context.panel.get("oos") if isinstance(context.panel,Mapping) else None
        required={"n","train_size","test_size","returns","return_series_by_definition","candidate_ids","families","pbo_return_series_by_definition","pbo_candidate_definition_hashes","pbo_candidate_definitions","knowledge_times_by_definition"}
        threshold_names={"fdr_q","dsr_floor","pbo_max","min_oos_fold_mean"}
        if isinstance(x,Mapping) and threshold_names.intersection(x):
            return self._block("panel cannot define validation thresholds")
        if not isinstance(x,Mapping) or not required <= set(x): return self._block("missing oos inputs",missing_input=sorted(required-(set(x) if isinstance(x,Mapping) else required)))
        try:
            subject_returns=tuple(x["returns"])
            n=x["n"]; train_size=x["train_size"]; test_size=x["test_size"]
            step=x.get("step",test_size); k=x.get("k",2); embargo=x.get("embargo",0)
            if any(type(value) is not int for value in (n,train_size,test_size,step,k,embargo)):
                raise ValueError("split parameters must be exact integers")
            if n != len(subject_returns) or n < 2 or train_size < 1 or test_size < 1 or step < 1 or k < 2 or k > n or embargo < 0:
                raise ValueError("invalid OOS split parameters")
            if any(type(value) is not float or not math.isfinite(value) for value in subject_returns):
                raise ValueError("subject returns must be exact finite floats")
            wf=walk_forward_splits(n,train_size,test_size,step); pk=purged_kfold(n,k,embargo)
            def validate_splits(splits, *, sequential):
                if not isinstance(splits,(tuple,list)) or not splits:
                    raise ValueError("OOS splits are required")
                means=[]
                for split in splits:
                    if not isinstance(split,(tuple,list)) or len(split) != 2:
                        raise ValueError("malformed OOS split")
                    train,test=split
                    if not isinstance(train,(tuple,list)) or not isinstance(test,(tuple,list)) or not train or not test:
                        raise ValueError("empty OOS split")
                    if any(type(index) is not int or not 0 <= index < n for index in (*train,*test)):
                        raise ValueError("invalid OOS split index")
                    if len(set(train)) != len(train) or len(set(test)) != len(test) or set(train).intersection(test):
                        raise ValueError("overlapping OOS split")
                    if sequential and max(train) >= min(test):
                        raise ValueError("walk-forward test must follow training")
                    means.append(math.fsum(subject_returns[index] for index in test) / len(test))
                return tuple(means)
            walk_forward_test_means=validate_splits(wf,sequential=True)
            purged_test_means=validate_splits(pk,sequential=False)
            ids=tuple(x["candidate_ids"])
            if not ids or len(set(ids)) != len(ids) or any(type(v) is not str or not v for v in ids): raise ValueError("invalid candidate ids")
            if "pvalues" in x or "family_pvalues" in x: raise ValueError("external p-values are not accepted")
            definitions=x["pbo_candidate_definitions"]
            if not isinstance(definitions,(tuple,list)) or len(definitions) != len(ids): raise ValueError("PBO definitions must align with candidates")
            definition_hashes=[]
            for pos, item in enumerate(definitions):
                if not isinstance(item,Mapping) or set(item) != {"candidate_id","definition"} or item["candidate_id"] != ids[pos]: raise ValueError("PBO definition ordering mismatch")
                definition_hashes.append(_digest({"canonical_definition":_canonical(item["definition"])}))
            if tuple(definition_hashes) != tuple(x["pbo_candidate_definition_hashes"]): raise ValueError("PBO definition hash mismatch")
            frozen_definitions=set(context.effective_definition_ids)
            if not frozen_definitions or frozen_definitions != set(definition_hashes): raise ValueError("OOS population does not match frozen trial set")
            return_population=x["return_series_by_definition"]
            if not isinstance(return_population,Mapping) or set(return_population) != frozen_definitions: raise ValueError("catalog return population mismatch")
            oos_indices=tuple(sorted({index for _, test in wf for index in test}))
            if len(oos_indices) < 2: raise ValueError("insufficient OOS observations for FDR")
            pmap={}; fdr_statistics={}
            for candidate_id_value, definition_hash in zip(ids,definition_hashes):
                values=tuple(return_population[definition_hash])
                if len(values) != n or any(type(value) is not float or not math.isfinite(value) for value in values): raise ValueError("FDR return population must contain aligned exact floats")
                oos_values=tuple(values[index] for index in oos_indices)
                mean=math.fsum(oos_values)/len(oos_values)
                variance=statistics.variance(oos_values)
                if variance == 0.0:
                    statistic=None
                    pvalue=0.0 if mean > 0.0 else 1.0
                else:
                    statistic=mean/math.sqrt(variance/len(oos_values))
                    pvalue=1.0-NormalDist().cdf(statistic)
                pmap[candidate_id_value]=pvalue
                fdr_statistics[candidate_id_value]={"definition_hash":definition_hash,"n_oos":len(oos_values),"mean":mean,"variance":variance,"statistic":statistic,"zero_variance":variance == 0.0,"pvalue":pvalue}
            oos_settings=context.policy.settings.get("oos",{}) if isinstance(context.policy.settings,Mapping) else {}
            if not isinstance(oos_settings,Mapping) or set(oos_settings)-threshold_names: raise ValueError("invalid OOS policy settings")
            q=oos_settings.get("fdr_q",.1); dsr_floor=oos_settings.get("dsr_floor",.95); pbo_max=oos_settings.get("pbo_max",.5); min_oos_fold_mean=oos_settings.get("min_oos_fold_mean",0.0)
            if any(type(v) not in (int,float) or isinstance(v,bool) or not math.isfinite(float(v)) or not 0.0 <= float(v) <= 1.0 for v in (q,dsr_floor,pbo_max)) or not 0.0 < float(q) <= 1.0: raise ValueError("invalid OOS thresholds")
            if type(min_oos_fold_mean) not in (int,float) or isinstance(min_oos_fold_mean,bool) or not math.isfinite(float(min_oos_fold_mean)): raise ValueError("invalid OOS fold threshold")
            pvals=tuple(pmap[c] for c in ids); bh=benjamini_hochberg(pvals,q); bhy=benjamini_hochberg_yekutieli(pvals,q)
            if not hasattr(x["families"],"family_of") or set(x["families"].family_of) != set(ids): raise ValueError("family assignment coverage mismatch")
            fam=hierarchical_fdr(pvalues=pmap,families=x["families"],q=q)
            candidate_id=x.get("candidate_id",ids[x.get("candidate_index",0)] if type(x.get("candidate_index",0)) is int and 0 <= x.get("candidate_index",0) < len(ids) else None)
            candidate_index=x.get("candidate_index",0)
            if type(candidate_index) is not int or not 0 <= candidate_index < len(ids): raise ValueError("invalid candidate index")
            subject_definition_hash=definition_hashes[candidate_index]
            if tuple(x["returns"]) != tuple(return_population.get(subject_definition_hash, ())): raise ValueError("subject returns do not match catalog population")
            catalog_evidence=create_catalog_evidence(subject_id=candidate_id,subject_definition_hash=subject_definition_hash,trial_definition_hashes=definition_hashes,return_series_by_definition=return_population)
            if not verify_catalog_evidence(catalog_evidence): raise ValueError("catalog evidence verification failed")
            if context.effective_trial_count is not None and catalog_evidence.trial_count != context.effective_trial_count: raise ValueError("catalog trial count does not match frozen ledger")
            dsr=catalog_evidence.deflated_sharpe
            candidate_exact=getattr(context.candidate,"exact_definition_id",None)
            if candidate_id != ids[candidate_index] or (candidate_exact is not None and candidate_exact != definition_hashes[candidate_index]): raise ValueError("PBO candidate identity mismatch")
            pbo_returns=x["pbo_return_series_by_definition"]
            if not isinstance(pbo_returns,Mapping) or set(pbo_returns) != frozen_definitions: raise ValueError("PBO return population mismatch")
            knowledge_by_definition=x["knowledge_times_by_definition"]
            if not isinstance(knowledge_by_definition,Mapping) or set(knowledge_by_definition) != frozen_definitions:
                raise ValueError("OOS knowledge-time population mismatch")
            for definition_hash in definition_hashes:
                timestamped=tuple(pbo_returns[definition_hash])
                knowledge_times=tuple(knowledge_by_definition[definition_hash])
                if len(timestamped) != n or len(knowledge_times) != n:
                    raise ValueError("PBO return series length mismatch")
                if any(type(value) is not int or value > context.event_at for value in knowledge_times):
                    raise ValueError("OOS knowledge time exceeds validation as-of")
                event_times=[]; timestamped_values=[]
                for observation in timestamped:
                    if not isinstance(observation,(tuple,list)) or len(observation) != 2:
                        raise ValueError("malformed timestamped OOS return")
                    event_time,value=observation
                    if type(event_time) is not int or event_time > context.event_at:
                        raise ValueError("OOS observation exceeds validation as-of")
                    if type(value) is not float or not math.isfinite(value):
                        raise ValueError("timestamped OOS returns must be exact finite floats")
                    event_times.append(event_time); timestamped_values.append(value)
                if event_times != sorted(set(event_times)):
                    raise ValueError("OOS event times must be sorted and unique")
                if any(knowledge_time < event_time for event_time,knowledge_time in zip(event_times,knowledge_times)):
                    raise ValueError("OOS knowledge time precedes event time")
                if tuple(timestamped_values) != tuple(return_population[definition_hash]):
                    raise ValueError("PBO returns do not match catalog population")
            pbo_evidence=create_batch_pbo_evidence(return_series_by_definition=pbo_returns)
            if tuple(pbo_evidence.candidate_definition_hashes) != tuple(sorted(definition_hashes)): raise ValueError("PBO evidence binding mismatch")
            if not verify_batch_pbo_evidence(pbo_evidence): raise ValueError("PBO evidence verification failed")
            pbo=pbo_evidence.pbo
            if not hasattr(fam,"selected_families") or not isinstance(fam.selected_families,(set,frozenset)) or not hasattr(fam,"survivors") or not isinstance(fam.survivors,(set,frozenset)): raise ValueError("malformed family FDR result")
            candidate_family=x.get("candidate_family")
            if type(candidate_index) is not int or not 0 <= candidate_index < len(ids) or candidate_index not in bh or candidate_index not in bhy or candidate_id != ids[candidate_index]: raise ValueError("candidate failed BH/BHY")
            if candidate_family is None: candidate_family=fam.selected_families.intersection({x["families"].family_of.get(candidate_id)}).pop() if hasattr(x["families"],"family_of") else None
            if not hasattr(x["families"], "family_of") or x["families"].family_of.get(candidate_id) != candidate_family or candidate_family is None or candidate_family not in fam.selected_families or candidate_id not in fam.survivors: raise ValueError("candidate failed family FDR")
        except Exception as exc: return self._block("oos engine unavailable",exception_type=type(exc).__name__)
        split_evidence={"walk_forward_test_means":walk_forward_test_means,"purged_test_means":purged_test_means,"min_oos_fold_mean":float(min_oos_fold_mean)}
        split_passed=all(value >= float(min_oos_fold_mean) for value in (*walk_forward_test_means,*purged_test_means))
        if not wf or not pk or not split_passed or not all(isinstance(v,(int,float)) and math.isfinite(v) and 0.0 <= v <= 1.0 for v in (dsr,pbo)) or dsr < dsr_floor or pbo > pbo_max or not bh or not bhy or not fam: return self._block("oos gate blocked",walk_forward_splits=len(wf),purged_splits=len(pk),split_evidence=split_evidence,fdr_pvalues=pmap,fdr_statistics=fdr_statistics,dsr=dsr,pbo=pbo,bh=sorted(bh),bhy=sorted(bhy),family_fdr={"selected_families":sorted(fam.selected_families),"family_pvalues":dict(fam.family_pvalues),"within_family_level":dict(fam.within_family_level),"n_families":fam.n_families,"reasons":dict(fam.reasons)})
        return self._pass(walk_forward_splits=len(wf),purged_splits=len(pk),split_evidence=split_evidence,fdr_pvalues=pmap,fdr_statistics=fdr_statistics,dsr=dsr,catalog_evidence=catalog_evidence.model_dump(mode="json"),pbo=pbo,pbo_evidence=pbo_evidence.model_dump(mode="json"),bh=sorted(bh),bhy=sorted(bhy),family_fdr={"selected_families":sorted(fam.selected_families),"family_pvalues":dict(fam.family_pvalues),"within_family_level":dict(fam.within_family_level),"n_families":fam.n_families,"reasons":dict(fam.reasons)})

class RealismValidationAdapter(_TypedAdapter):
    name="realism"; implementation_id="services.research.realism.capacity_curve;tail_risk;validation.regime;CostModel:1"
    def evaluate(self, context):
        from services.research.realism.capacity_curve import capacity_band
        from services.research.realism.tail_risk import historical_var_cvar,hill_tail_index,lower_tail_dependence
        from services.validation.regime import regime_conditional_stats, label_regimes_by_vol, regime_profitable_frac
        from quantos.contracts.cost import CostModel
        x=context.panel.get("realism") if isinstance(context.panel,Mapping) else None
        required={"gross_returns","gross_event_times","gross_knowledge_times","turnover_by_period","aum_grid","impact_coef_lo_hi","returns","event_times","knowledge_times","market_closes","benchmark_returns","cost_model","trade_notional","trade_shares","adv_shares"}
        if not isinstance(x,Mapping) or not required <= set(x): return self._block("missing realism inputs",missing_input=sorted(required-(set(x) if isinstance(x,Mapping) else required)))
        event_times=tuple(x["event_times"]); gross_event_times=tuple(x["gross_event_times"])
        knowledge_times=tuple(x["knowledge_times"]); gross_knowledge_times=tuple(x["gross_knowledge_times"])
        if (
            len(event_times) != len(x["returns"])
            or len(knowledge_times) != len(x["returns"])
            or len(gross_event_times) != len(x["gross_returns"])
            or len(gross_knowledge_times) != len(x["gross_returns"])
            or any(type(value) is not int or value > context.event_at for value in (*event_times,*gross_event_times,*knowledge_times,*gross_knowledge_times))
            or list(event_times) != sorted(set(event_times))
            or list(gross_event_times) != sorted(set(gross_event_times))
            or any(knowledge_time < event_time for event_time,knowledge_time in zip(event_times,knowledge_times))
            or any(knowledge_time < event_time for event_time,knowledge_time in zip(gross_event_times,gross_knowledge_times))
        ):
            return self._block("realism inputs exceed validation as-of")
        cost=x["cost_model"]
        if not isinstance(cost,CostModel) or not all(type(getattr(cost,k)) in (int,float) and math.isfinite(float(getattr(cost,k))) and float(getattr(cost,k)) >= 0.0 for k in ("fee_bps","slippage_bps","impact_coef")): return self._block("typed finite CostModel required")
        if not all(_finite_tree(x.get(k)) for k in ("gross_returns","turnover_by_period","aum_grid","impact_coef_lo_hi","returns","market_closes","benchmark_returns","trade_notional","trade_shares","adv_shares")): return self._block("nonfinite realism input")
        if not x["gross_returns"] or len(x["gross_returns"]) != len(x["turnover_by_period"]): return self._block("turnover periods must align with returns")
        if not x["returns"] or len(x["returns"]) != len(x["benchmark_returns"]) or len(x["returns"]) != len(x["market_closes"]): return self._block("realism series must align")
        for period in x["turnover_by_period"]:
            if not isinstance(period,(tuple,list)) or not period: return self._block("turnover period malformed")
            for leg in period:
                if not isinstance(leg,(tuple,list)) or len(leg) != 2 or any(type(v) not in (int,float) or not math.isfinite(float(v)) for v in leg): return self._block("turnover leg malformed")
        try:
            band=capacity_band(x["gross_returns"],x["turnover_by_period"],x["aum_grid"],x["impact_coef_lo_hi"]); regime_labels=label_regimes_by_vol(x["market_closes"])
            if len(regime_labels) != len(x["returns"]) or sum(v is not None for v in regime_labels) < 2: raise ValueError("regime labels do not align")
            regime_profitable=regime_profitable_frac(x["returns"],regime_labels); tail=historical_var_cvar(x["returns"],x.get("alpha",.95)); hill=hill_tail_index(x["returns"],x.get("tail_q",.1)); ltd=lower_tail_dependence(x["returns"],x["benchmark_returns"],x.get("tail_q",.1)); regime=regime_conditional_stats(x["returns"],regime_labels); cost_value=cost.trade_cost(x["trade_notional"],x["trade_shares"],x["adv_shares"])
        except Exception as exc: return self._block("realism engine unavailable",exception_type=type(exc).__name__)
        if not isinstance(band,Mapping) or not _finite_tree(band) or band.get("aum_low") is None or not isinstance(tail,Mapping) or not _finite_tree(tail) or tail.get("cvar") is None or hill is None or not isinstance(hill,(int,float)) or not math.isfinite(float(hill)) or ltd is None or not isinstance(ltd,(int,float)) or not math.isfinite(float(ltd)) or not 0.0 <= float(ltd) <= 1.0 or not isinstance(regime,Mapping) or not _finite_tree(regime) or not math.isfinite(cost_value): return self._block("realism gate blocked")
        return self._pass(capacity=band,tail=tail,hill_tail_index=hill,lower_tail_dependence=ltd,regime={str(k): v for k, v in regime.items()},regime_profitable_frac=regime_profitable,cost=cost_value)

def production_adapters() -> tuple[ValidationStage,...]: return (CheapValidationAdapter(),OOSValidationAdapter(),RealismValidationAdapter())
_DEFAULT_ADAPTERS=production_adapters()
_PRODUCTION_EVALUATORS={type(x): type(x).evaluate for x in _DEFAULT_ADAPTERS}

@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    stages: tuple[ValidationStage,...]=field(default_factory=tuple); settings: Mapping[str,Any]=field(default_factory=dict)
    def __post_init__(self):
        stages=tuple(self.stages) or _DEFAULT_ADAPTERS
        if tuple(getattr(s,"name",None) for s in stages)!=_STAGE_ORDER: raise ValueError("validation stages must be exactly cheap, oos, realism")
        if any(type(getattr(s,"version",None)) is not str or not s.version or type(getattr(s,"implementation_id",None)) is not str or not s.implementation_id for s in stages): raise ValueError("every validation stage needs identity")
        object.__setattr__(self,"stages",stages); object.__setattr__(self,"settings",_freeze(self.settings))
    @property
    def digest(self): return _digest({"stages":[{"name":s.name,"version":s.version,"implementation_id":s.implementation_id} for s in self.stages],"settings":self.settings})

@dataclass(frozen=True, slots=True)
class ValidationArtifact:
    trial_id:str; alpha_id:str; family_id:str; exact_definition_id:str; panel_digest:str; policy_digest:str; event_at:int; effective_trial_ids:tuple[str,...]; trial_set_digest:str; stage_evidence_hashes:Mapping[str,str]; stage_receipt_bindings:Mapping[str,Mapping[str,Any]]; effective_trial_count:int; verdict:str; reason:str|None=None; stage_metadata:Mapping[str,Mapping[str,str]]=field(default_factory=dict); artifact_commitment:str|None=None
    def __post_init__(self):
        if any(type(v) is not str or not _HEX64.fullmatch(v) for v in (self.trial_id,self.alpha_id,self.family_id,self.exact_definition_id,self.panel_digest,self.policy_digest)): raise ValueError("invalid validation artifact identity")
        if type(self.event_at) is not int or isinstance(self.event_at,bool): raise ValueError("event_at must be deterministic integer event time")
        trial_ids=tuple(self.effective_trial_ids)
        if len(trial_ids) != self.effective_trial_count or any(type(v) is not str or not _HEX64.fullmatch(v) for v in trial_ids) or self.trial_set_digest != _digest(list(trial_ids)): raise ValueError("invalid frozen trial-set commitment")
        object.__setattr__(self,"effective_trial_ids",trial_ids)
        if self.verdict not in _TERMINAL or type(self.effective_trial_count) is not int or self.effective_trial_count<0: raise ValueError("invalid validation artifact")
        if set(self.stage_evidence_hashes)!=set(_STAGE_ORDER) or any(type(v) is not str or not _HEX64.fullmatch(v) for v in self.stage_evidence_hashes.values()): raise ValueError("complete lowercase stage hashes required")
        if not isinstance(self.stage_receipt_bindings,Mapping) or set(self.stage_receipt_bindings)!=set(_STAGE_ORDER) or any(set(v)!={"global_seq","row_hash"} or type(v["global_seq"]) is not int or not _HEX64.fullmatch(str(v["row_hash"])) for v in self.stage_receipt_bindings.values()): raise ValueError("complete receipt bindings required")
        if not isinstance(self.stage_metadata,Mapping) or set(self.stage_metadata)!=set(_STAGE_ORDER) or any(set(v)!={"stage","version","implementation_id"} for v in self.stage_metadata.values()): raise ValueError("complete stage metadata required")
        if type(self.artifact_commitment) is not str or not _HEX64.fullmatch(self.artifact_commitment): raise ValueError("artifact commitment required")
        body={"trial_id":self.trial_id,"alpha_id":self.alpha_id,"family_id":self.family_id,"exact_definition_id":self.exact_definition_id,"panel_digest":self.panel_digest,"policy_digest":self.policy_digest,"event_at":self.event_at,"effective_trial_ids":self.effective_trial_ids,"trial_set_digest":self.trial_set_digest,"stage_evidence_hashes":self.stage_evidence_hashes,"stage_receipt_bindings":self.stage_receipt_bindings,"effective_trial_count":self.effective_trial_count,"verdict":self.verdict,"reason":self.reason,"stage_metadata":self.stage_metadata}
        if _digest(body) != self.artifact_commitment: raise ValueError("artifact commitment mismatch")
        object.__setattr__(self,"stage_evidence_hashes",_freeze(self.stage_evidence_hashes)); object.__setattr__(self,"stage_receipt_bindings",_freeze(self.stage_receipt_bindings)); object.__setattr__(self,"stage_metadata",_freeze(self.stage_metadata))

def _identity(candidate):
    definition=getattr(candidate,"definition",None)
    if definition is not None:
        if not isinstance(definition,AlphaDefinition): raise ValueError("alpha_id canonical definition required")
        alpha=canonical_alpha_id(definition); family=canonical_family_id(definition); exact=_digest({"canonical_definition":canonical_alpha_json(definition)})
    else:
        alpha=getattr(candidate,"alpha_id",None); family=getattr(candidate,"family_id",None); exact=getattr(candidate,"exact_definition_id",None)
        if not all(type(v) is str and _HEX64.fullmatch(v) for v in (alpha,family,exact)): raise ValueError("complete explicit 64hex identity envelope required")
    for attr,expected in (("alpha_id",alpha),("family_id",family),("exact_definition_id",exact)):
        supplied=getattr(candidate,attr,None)
        if supplied is not None and supplied!=expected: raise ValueError(f"{attr} identity mismatch")
    return alpha,family,exact

def _exception_evidence(stage,version,implementation,exc): return StageEvidence(stage,version,"BLOCK",{"exception_type":type(exc).__name__,"exception_code":type(exc).__name__},f"provider exception: {type(exc).__name__}",implementation)

def _receipt_evidence(stage, receipt):
    payload=receipt[stage]["payload"]; return payload["evidence_hash"]


def _complete_or_recover(ledger, trial_id, stage, state, payload, *, owner, now, attempt):
    try: ledger.complete_validation_stage(trial_id,stage,state,payload,owner=owner,now=now,attempt=attempt)
    except TrialLedgerConflict:
        current=ledger.validation_receipt(trial_id).get(stage)
        if not current or current["state"] not in {"DONE","BLOCKED","FAILED"}: raise


def _frozen_trial_snapshot(ledger: TrialLedger, trial_id: str, family_id: str) -> EffectiveTrialCount:
    receipts = ledger.validation_receipt(trial_id)
    terminal = [
        receipts[stage]["payload"]
        for stage in _STAGE_ORDER
        if stage in receipts
        and receipts[stage]["state"] in {"DONE", "BLOCKED", "FAILED"}
    ]
    current = ledger.effective_trial_count(family_id)
    if not terminal:
        return current
    identities = {
        (payload.get("effective_trial_count"), payload.get("trial_set_digest"))
        for payload in terminal
    }
    if len(identities) != 1:
        raise TrialLedgerError("validation receipts disagree on frozen trial set")
    saved_count, saved_digest = identities.pop()
    if (
        type(saved_count) is not int
        or saved_count < 1
        or saved_count > current.count
        or type(saved_digest) is not str
        or not _HEX64.fullmatch(saved_digest)
    ):
        raise TrialLedgerError("invalid frozen trial set receipt")
    frozen_ids = current.trial_ids[:saved_count]
    if _digest(list(frozen_ids)) != saved_digest:
        raise TrialLedgerError("frozen trial set cannot be reconstructed")
    return EffectiveTrialCount(saved_count, frozen_ids)


def _run_validation_dag(candidate:Any,panel:Any,policy:ValidationPolicy,ledger:TrialLedger,*,event_at:int,owner:str="validation",lease_seconds:int=60,lease_now:int,wait_timeout_seconds:float|None=None,production:bool=False)->ValidationArtifact:
    if not isinstance(policy,ValidationPolicy) or not isinstance(ledger,TrialLedger): raise TypeError("policy and ledger types required")
    if production:
        if tuple(type(x) for x in policy.stages) != tuple(type(x) for x in _DEFAULT_ADAPTERS): raise ValueError("production DAG requires registered typed adapters")
        for stage, canonical in zip(policy.stages, _DEFAULT_ADAPTERS):
            if (type(stage), stage.name, stage.version, stage.implementation_id) != (type(canonical), canonical.name, canonical.version, canonical.implementation_id): raise ValueError("production adapter identity is not canonical")
            if getattr(stage.evaluate, "__func__", None) is not _PRODUCTION_EVALUATORS.get(type(stage)): raise ValueError("production adapter implementation is not sealed")
    if type(event_at) is not int or isinstance(event_at,bool) or type(lease_now) is not int or isinstance(lease_now,bool): raise ValueError("event_at and lease_now must be deterministic ints")
    if wait_timeout_seconds is None: wait_timeout_seconds=float(lease_seconds)
    if type(wait_timeout_seconds) not in (int,float) or not math.isfinite(float(wait_timeout_seconds)) or wait_timeout_seconds <= 0: raise ValueError("wait_timeout_seconds must be finite positive")
    definition=getattr(candidate,"definition",None)
    if isinstance(definition,AlphaDefinition):
        declared_policy=definition.validation_policy
        declared=declared_policy.get("digest") if isinstance(declared_policy,Mapping) and set(declared_policy)=={"digest"} else None
    else:
        declared=getattr(candidate,"validation_policy_digest",None)
    if declared is None or declared!=policy.digest: raise ValueError("candidate validation policy digest mismatch")
    alpha,family,exact=_identity(candidate); execution_panel=_execution_snapshot(panel); panel_digest=_panel_digest(execution_panel); trial_id=_digest({"alpha_id":alpha,"family_id":family,"exact_definition_id":exact,"panel_digest":panel_digest,"policy_digest":policy.digest,"event_at":event_at,"trust_mode":"PRODUCTION" if production else "TEST"})
    try: ledger.append(TrialRecord(trial_id,alpha,family,exact,policy.digest,"PLANNED",0,event_at))
    except TrialLedgerConflict:
        existing=ledger.load(trial_id)
        if any((e.alpha_id,e.family_id,e.exact_definition_id,e.validation_policy_digest)!=(alpha,family,exact,policy.digest) for e in existing.events): raise
    aggregate=ledger.load(trial_id);
    if aggregate.events[0].at != event_at: raise TrialLedgerConflict("event_at identity mismatch")
    receipts=ledger.validation_receipt(trial_id); ar=receipts.get("__artifact__")
    if ar:
        restored=ledger.load_validation_artifact(trial_id,panel_digest=panel_digest,policy_digest=policy.digest)
        return ValidationArtifact(**restored)
    if aggregate.status=="PLANNED":
        try:
            ledger.append(TrialRecord(trial_id,alpha,family,exact,policy.digest,"RUNNING",1,event_at))
        except TrialLedgerError:
            for _ in range(10000):
                ar=ledger.validation_receipt(trial_id).get("__artifact__")
                if ar:
                    return ValidationArtifact(**ledger.load_validation_artifact(trial_id,panel_digest=panel_digest,policy_digest=policy.digest))
            raise
    trial_snapshot=_frozen_trial_snapshot(ledger,trial_id,family)
    trial_set_digest=_digest(list(trial_snapshot.trial_ids))
    frozen_definitions=tuple(ledger.load(item).exact_definition_id for item in trial_snapshot.trial_ids)
    context=ValidationContext(candidate,execution_panel,policy,trial_id,panel_digest,policy.digest,event_at,effective_trial_count=max(1,trial_snapshot.count),effective_trial_ids=trial_snapshot.trial_ids,trial_set_digest=trial_set_digest,effective_definition_ids=frozen_definitions); hashes={}; metadata={}; verdict="PASS"; reason=None
    for pos,stage in enumerate(policy.stages):
        try:
            claim_result=ledger.claim_validation_stage(trial_id,stage.name,owner=owner,now=lease_now,lease_seconds=lease_seconds,panel_digest=panel_digest,policy_digest=policy.digest)
            claim, claim_attempt=claim_result.state, claim_result.attempt
        except TrialLedgerError:
            deadline=time.monotonic()+float(wait_timeout_seconds)
            for attempt in range(10000):
                current_receipts=ledger.validation_receipt(trial_id)
                if current_receipts.get("__artifact__"):
                    return ValidationArtifact(**ledger.load_validation_artifact(trial_id,panel_digest=panel_digest,policy_digest=policy.digest))
                current_stage=current_receipts.get(stage.name)
                if current_stage and current_stage["state"] in {"DONE","BLOCKED","FAILED"}:
                    claim=current_stage["state"]
                    claim_attempt=current_stage["attempt"]
                    break
                if time.monotonic() >= deadline: raise TrialLedgerConflict("validation stage wait timeout")
                time.sleep(min(0.01, 0.0005 * (attempt + 1)))
            else:
                raise TrialLedgerConflict("validation stage wait timeout")
        if claim == "IN_PROGRESS":
            claim = "IN_PROGRESS"
            deadline=time.monotonic()+float(wait_timeout_seconds)
            for attempt in range(10000):
                current=ledger.validation_receipt(trial_id).get(stage.name)
                if current and current["state"] in {"DONE","BLOCKED","FAILED"}:
                    claim=current["state"]; claim_attempt=current["attempt"]; break
                if time.monotonic() >= deadline: raise TrialLedgerConflict("validation stage wait timeout")
                time.sleep(min(0.01, 0.0005 * (attempt + 1)))
            if claim == "IN_PROGRESS": raise TrialLedgerConflict("validation stage wait timeout")
        if claim in {"DONE","BLOCKED","FAILED"}:
            current=ledger.validation_receipt(trial_id)[stage.name]; hashes[stage.name]=current["payload"]["evidence_hash"]; metadata[stage.name]={"stage":stage.name,"version":stage.version,"implementation_id":stage.implementation_id};
            if current["payload"].get("outcome")=="BLOCK": verdict="BLOCK"; reason=reason or current["payload"].get("reason")
            if verdict=="BLOCK":
                # deterministic short-circuit receipts are handled below
                break
            continue
        try:
            evidence=stage.evaluate(context)
            if not isinstance(evidence,StageEvidence) or evidence.stage!=stage.name or evidence.version!=stage.version or evidence.implementation_id!=stage.implementation_id: raise ValueError("stage evidence identity mismatch")
        except Exception as exc: evidence=_exception_evidence(stage.name,stage.version,stage.implementation_id,exc); reason=f"provider exception: {type(exc).__name__}"
        hashes[stage.name]=evidence.evidence_hash; metadata[stage.name]={"stage":stage.name,"version":stage.version,"implementation_id":stage.implementation_id}
        _complete_or_recover(ledger,trial_id,stage.name,"DONE" if evidence.outcome=="PASS" else "BLOCKED",{"trial_id":trial_id,"panel_digest":panel_digest,"policy_digest":policy.digest,"event_at":event_at,"effective_trial_count":trial_snapshot.count,"trial_set_digest":trial_set_digest,"evidence_hash":evidence.evidence_hash,"stage":stage.name,"version":stage.version,"implementation_id":stage.implementation_id,"outcome":evidence.outcome,"evidence":evidence.evidence,"reason":evidence.reason},owner=owner,now=lease_now,attempt=claim_attempt)
        if evidence.outcome=="BLOCK": verdict="BLOCK"; reason=reason or evidence.reason; break
    blocked_stage=next((s for s in policy.stages if s.name not in hashes),None)
    while blocked_stage:
        evidence=StageEvidence(blocked_stage.name,blocked_stage.version,"BLOCK",{"short_circuit":True,"upstream":"blocked"},"short-circuit after upstream BLOCK",blocked_stage.implementation_id); hashes[blocked_stage.name]=evidence.evidence_hash; metadata[blocked_stage.name]={"stage":blocked_stage.name,"version":blocked_stage.version,"implementation_id":blocked_stage.implementation_id};
        try:
            claim_result=ledger.claim_validation_stage(trial_id,blocked_stage.name,owner=owner,now=lease_now,lease_seconds=lease_seconds,panel_digest=panel_digest,policy_digest=policy.digest)
            claim, claim_attempt=claim_result.state, claim_result.attempt
        except TrialLedgerError:
            deadline=time.monotonic()+float(wait_timeout_seconds)
            for attempt in range(10000):
                ar=ledger.validation_receipt(trial_id).get("__artifact__")
                if ar: return ValidationArtifact(**ledger.load_validation_artifact(trial_id,panel_digest=panel_digest,policy_digest=policy.digest))
                if time.monotonic() >= deadline: raise TrialLedgerConflict("validation artifact wait timeout")
                time.sleep(min(0.01, 0.0005 * (attempt + 1)))
            raise TrialLedgerConflict("validation artifact wait timeout")
        if claim == "CLAIMED":
            _complete_or_recover(ledger,trial_id,blocked_stage.name,"BLOCKED",{"trial_id":trial_id,"panel_digest":panel_digest,"policy_digest":policy.digest,"event_at":event_at,"effective_trial_count":trial_snapshot.count,"trial_set_digest":trial_set_digest,"evidence_hash":evidence.evidence_hash,"stage":blocked_stage.name,"version":blocked_stage.version,"implementation_id":blocked_stage.implementation_id,"outcome":"BLOCK","evidence":evidence.evidence,"reason":evidence.reason},owner=owner,now=lease_now,attempt=claim_attempt)
        elif claim == "IN_PROGRESS":
            deadline=time.monotonic()+float(wait_timeout_seconds)
            while time.monotonic() < deadline:
                current=ledger.validation_receipt(trial_id).get(blocked_stage.name)
                if current and current["state"] in {"DONE","BLOCKED","FAILED"}: break
                time.sleep(0.01)
            else: raise TrialLedgerConflict("validation stage wait timeout")
        blocked_stage=next((s for s in policy.stages if s.name not in hashes),None)
    status="PASSED" if verdict=="PASS" else "FAILED"
    current=ledger.load(trial_id)
    if current.status=="RUNNING": ledger.append(TrialRecord(trial_id,alpha,family,exact,policy.digest,status,2,event_at))
    count=trial_snapshot.count
    base={"trial_id":trial_id,"alpha_id":alpha,"family_id":family,"exact_definition_id":exact,"panel_digest":panel_digest,"policy_digest":policy.digest,"event_at":event_at,"effective_trial_ids":trial_snapshot.trial_ids,"trial_set_digest":trial_set_digest,"stage_evidence_hashes":dict(hashes),"stage_receipt_bindings":{k:{"global_seq":v["global_seq"],"row_hash":v["row_hash"]} for k,v in ledger.validation_receipt(trial_id).items() if k in _STAGE_ORDER},"effective_trial_count":count,"verdict":verdict,"reason":reason,"stage_metadata":metadata}
    base["artifact_commitment"]=_digest(base); artifact=ValidationArtifact(**base); ledger.save_validation_artifact(trial_id,base); return artifact

def run_validation_dag(candidate:Any,panel:Any,policy:ValidationPolicy,ledger:TrialLedger,*,event_at:int,owner:str="validation",lease_seconds:int=60,lease_now:int,wait_timeout_seconds:float|None=None,production:bool=False)->ValidationArtifact:
    if not isinstance(ledger, TrialLedger):
        raise TypeError("policy and ledger types required")
    # Serialize orchestration for a shared ledger object; SQLite remains the durable cross-process authority.
    with ledger._lock:
        return _run_validation_dag(candidate,panel,policy,ledger,event_at=event_at,owner=owner,lease_seconds=lease_seconds,lease_now=lease_now,wait_timeout_seconds=wait_timeout_seconds,production=production)

__all__=["ValidationStage","ValidationPolicy","ValidationContext","StageEvidence","ValidationArtifact","CheapValidationAdapter","OOSValidationAdapter","RealismValidationAdapter","production_adapters","RegisteredLeakageCompute","register_leakage_compute","run_validation_dag"]

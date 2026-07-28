from __future__ import annotations

import dataclasses
import inspect
import re

import pytest

from services.research.experiment_registry import family_schema_spec as spec


def _immutable_triggers() -> tuple[spec.TriggerPolicySpec, ...]:
    return (
        spec.TriggerPolicySpec(
            name="sample_rows_no_update",
            kind=spec.TriggerPolicyKind.NO_UPDATE,
            event=spec.TriggerEvent.UPDATE,
            error_code="AUTHORITY_ROW_IMMUTABLE",
        ),
        spec.TriggerPolicySpec(
            name="sample_rows_no_delete",
            kind=spec.TriggerPolicyKind.NO_DELETE,
            event=spec.TriggerEvent.DELETE,
            error_code="AUTHORITY_ROW_IMMUTABLE",
        ),
    )


def _table_spec() -> spec.AuthorityTableSpec:
    columns = (
        spec.ColumnSpec("family_id", spec.SqlType.TEXT, nullable=False),
        spec.ColumnSpec("sequence_no", spec.SqlType.INTEGER, nullable=False),
        spec.ColumnSpec("state", spec.SqlType.TEXT, nullable=False),
        spec.ColumnSpec("prev_record_hash", spec.SqlType.TEXT, nullable=False),
        spec.ColumnSpec("record_hash", spec.SqlType.TEXT, nullable=False),
    )
    state_check = spec.CheckConstraintSpec(
        name="sample_rows_state_check",
        expression=spec.MembershipExpression(
            spec.ColumnValue("state"),
            (spec.LiteralValue("OPEN"), spec.LiteralValue("CLOSED")),
        ),
    )
    terminal_guard = spec.TriggerPolicySpec(
        name="sample_rows_terminal_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="TERMINAL_CHAIN_CLOSED",
        violation_when=spec.RelationExistsExpression(
            table="sample_rows",
            bindings=(
                spec.LookupBinding(
                    related_column="family_id",
                    value=spec.ColumnValue("family_id", spec.RowSource.NEW),
                ),
            ),
            predicate=spec.ComparisonExpression(
                spec.ColumnValue("state", spec.RowSource.RELATED),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("CLOSED"),
            ),
        ),
        state_machine=spec.InsertStateMachineSpec(
            state_column="state",
            initial_states=("OPEN",),
            transitions=(spec.InsertStateTransition("OPEN", "CLOSED"),),
            terminal_states=("CLOSED",),
            sequence_column="sequence_no",
            identity_columns=("family_id",),
        ),
    )
    return spec.AuthorityTableSpec(
        table_name="sample_rows",
        columns=columns,
        primary_key_columns=("family_id", "sequence_no"),
        foreign_keys=(
            spec.ForeignKeySpec(
                columns=("family_id",),
                referenced_table="families",
                referenced_columns=("family_id",),
                on_update=spec.ForeignKeyAction.RESTRICT,
                on_delete=spec.ForeignKeyAction.RESTRICT,
            ),
        ),
        indexes=(
            spec.IndexSpec(
                name="sample_rows_one_closed",
                columns=(spec.IndexColumnSpec("family_id"),),
                unique=True,
                predicate=spec.ComparisonExpression(
                    spec.ColumnValue("state"),
                    spec.ComparisonOperator.EQ,
                    spec.LiteralValue("CLOSED"),
                ),
            ),
        ),
        checks=(state_check,),
        triggers=(*_immutable_triggers(), terminal_guard),
        row_hash=spec.RowHashSpec(
            canonical_fields=(
                "family_id",
                "sequence_no",
                "state",
                "prev_record_hash",
            ),
            version_literal="SAMPLE_ROW_V1",
            record_hash_column="record_hash",
        ),
        chain=spec.ChainSpec(
            partition_key_columns=("family_id",),
            ordinal_column="sequence_no",
            genesis_rule=spec.ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH,
            previous_hash_column="prev_record_hash",
            record_hash_column="record_hash",
            terminal_head_rule=spec.ChainHeadRule.LATEST_TERMINAL_RECORD_HASH,
        ),
        verification_rules=(
            spec.VerificationRuleSpec(
                "sample_rows_hashes",
                spec.VerificationRuleKind.ROW_HASH,
                columns=("record_hash",),
            ),
            spec.VerificationRuleSpec(
                "sample_rows_family_totality",
                spec.VerificationRuleKind.CROSS_TABLE_TOTALITY,
                columns=("family_id",),
                referenced_table="families",
            ),
        ),
    )


def test_column_specs_defaults_and_expression_nodes_are_frozen_and_closed() -> None:
    default = spec.ColumnDefault(spec.DefaultKind.INTEGER, 0)
    column = spec.ColumnSpec("sequence_no", spec.SqlType.INTEGER, nullable=False, default=default)
    expression = spec.LogicalExpression(
        spec.LogicalOperator.AND,
        (
            spec.ComparisonExpression(
                spec.ColumnValue("sequence_no"),
                spec.ComparisonOperator.GE,
                spec.LiteralValue(0),
            ),
            spec.NullExpression(spec.ColumnValue("sequence_no"), spec.NullOperator.IS_NOT_NULL),
        ),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        column.nullable = True  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        expression.operator = spec.LogicalOperator.OR  # type: ignore[misc]
    with pytest.raises(ValueError, match="identifier"):
        spec.ColumnSpec("value TEXT CHECK (1)", spec.SqlType.TEXT)
    with pytest.raises((TypeError, ValueError), match="DefaultKind|default"):
        spec.ColumnDefault(spec.DefaultKind.INTEGER, "0")
    with pytest.raises(ValueError):
        spec.SqlType("VARCHAR")

    class RawPredicate:
        sql = "1 = 1"

    with pytest.raises(TypeError, match="closed predicate"):
        spec.CheckConstraintSpec("unsafe_check", RawPredicate())  # type: ignore[arg-type]


def test_ordered_contract_derivation_is_explicit_and_cannot_pose_as_lookup() -> None:
    coverage = spec.ContractFieldCoverageSpec(
        contract_type="SampleContract",
        field_name="ordered_states",
        kind=spec.CoverageKind.NON_PERSISTED_DERIVATION,
        derivation_id="sample.ordered_states",
    )
    derivation = spec.ContractFieldDerivationSpec(
        derivation_id="sample.ordered_states",
        contract_type="SampleContract",
        field_name="ordered_states",
        kind=spec.ContractDerivationKind.ORDERED_ROW_AGGREGATION,
        binding_table="sample_rows",
        binding_field="family_id",
        source_table="sample_rows",
        source_key_field="family_id",
        source_value_field="state",
        order_by_fields=("sequence_no",),
    )
    component = spec.SchemaComponentSpec(
        component_id="SAMPLE_ORDERED_DERIVATION_V1",
        capabilities=("sample.ordered_derivation",),
        tables=(_table_spec(),),
        coverage=(coverage,),
        contract_derivations=(derivation,),
    )
    assert component.contract_derivations == (derivation,)

    with pytest.raises(ValueError, match="requires fixed ordering"):
        spec.ContractFieldDerivationSpec(
            derivation_id="sample.missing_order",
            contract_type="SampleContract",
            field_name="ordered_states",
            kind=spec.ContractDerivationKind.ORDERED_ROW_AGGREGATION,
            binding_table="sample_rows",
            binding_field="family_id",
            source_table="sample_rows",
            source_key_field="family_id",
            source_value_field="state",
        )
    with pytest.raises(ValueError, match="cannot declare ordering"):
        spec.ContractFieldDerivationSpec(
            derivation_id="sample.fake_lookup",
            contract_type="SampleContract",
            field_name="ordered_states",
            kind=spec.ContractDerivationKind.IMMUTABLE_ROW_LOOKUP,
            binding_table="sample_rows",
            binding_field="family_id",
            source_table="sample_rows",
            source_key_field="family_id",
            source_value_field="state",
            order_by_fields=("sequence_no",),
        )


def _artifact_graph_table_spec() -> spec.AuthorityTableSpec:
    table = _table_spec()
    return dataclasses.replace(
        table,
        columns=(
            *table.columns,
            spec.ColumnSpec("published_artifact_envelope_hash", spec.SqlType.TEXT, nullable=False),
            spec.ColumnSpec("target_content_addressed_path", spec.SqlType.TEXT, nullable=False),
        ),
    )


def _artifact_graph_derivations() -> tuple[
    spec.ContentAddressedArtifactGraphDerivationSpec,
    spec.ContentAddressedArtifactGraphDerivationSpec,
]:
    envelope_graph = spec.ContentAddressedArtifactGraphDerivationSpec(
        derivation_id="c9.publication.exact_byte_graph.v1",
        locator=spec.ArtifactGraphLocatorSpec(
            kind=spec.ArtifactGraphLocatorSourceKind.PERSISTED_ROW,
            binding_table="sample_rows",
            hash_field="published_artifact_envelope_hash",
            path_field="target_content_addressed_path",
        ),
        contract_surfaces=(
            spec.ArtifactGraphContractSpec(
                contract_type="ArtifactCore",
                field_names=("record_type", "authority_instance_id", "authority_projection_hash"),
            ),
            spec.ArtifactGraphContractSpec(
                contract_type="ArtifactCoreScanReceipt",
                field_names=("record_type", "artifact_core_hash", "scan_receipt_hash"),
            ),
            spec.ArtifactGraphContractSpec(
                contract_type="PublishedArtifactEnvelope",
                field_names=("record_type", "artifact_core", "artifact_core_scan_receipt"),
            ),
        ),
        verifier_code="C9_VERIFY_ENVELOPE_EXACT_BYTE_ARTIFACT_GRAPH_V1",
    )
    projection_graph = spec.ContentAddressedArtifactGraphDerivationSpec(
        derivation_id="c9.publication.projection.exact_byte_graph.v1",
        locator=spec.ArtifactGraphLocatorSpec(
            kind=spec.ArtifactGraphLocatorSourceKind.CONTRACT_INPUT,
            contract_type="DataSnoopingEvidenceRef",
            hash_field="publication_projection_hash",
            path_field="publication_projection_path",
        ),
        contract_surfaces=(
            spec.ArtifactGraphContractSpec(
                contract_type="ArtifactPublicationReceiptProjection",
                field_names=("record_type", "publication_receipt", "publication_chain_prefix"),
            ),
        ),
        verifier_code="C9_VERIFY_PUBLICATION_PROJECTION_EXACT_BYTES_V1",
    )
    return envelope_graph, projection_graph


def _artifact_graph_coverage(
    *graphs: spec.ContentAddressedArtifactGraphDerivationSpec,
) -> tuple[spec.ContractFieldCoverageSpec, ...]:
    return tuple(
        spec.ContractFieldCoverageSpec(
            contract_type=surface.contract_type,
            field_name=field_name,
            kind=spec.CoverageKind.NON_PERSISTED_DERIVATION,
            derivation_id=graph.derivation_id,
        )
        for graph in graphs
        for surface in graph.contract_surfaces
        for field_name in surface.field_names
    )


def test_content_addressed_artifact_graph_derivation_is_exact_byte_and_fail_closed() -> None:
    envelope_graph, projection_graph = _artifact_graph_derivations()
    graphs = (envelope_graph, projection_graph)
    component = spec.SchemaComponentSpec(
        component_id="C9_EXACT_BYTE_GRAPH_SAMPLE_V1",
        capabilities=("artifact.publication.exact_byte_graph",),
        tables=(_artifact_graph_table_spec(),),
        coverage=_artifact_graph_coverage(*graphs),
        artifact_graph_derivations=graphs,
    )

    assert component.artifact_graph_derivations == graphs
    for graph in graphs:
        assert graph.kind is spec.ArtifactGraphDerivationKind.CONTENT_ADDRESSED_CANONICAL_EXACT_BYTES
        assert graph.canonical_encoding is spec.CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1
        assert graph.hash_algorithm is spec.RowHashAlgorithm.SHA256
        assert graph.cardinality is spec.RelatedLookupCardinality.EXACTLY_ONE
        assert graph.failure_policy is spec.CanonicalJsonFailurePolicy.REJECT
        assert not any("sql" in field.name.lower() for field in dataclasses.fields(graph))
    assert envelope_graph.locator == spec.ArtifactGraphLocatorSpec(
        kind=spec.ArtifactGraphLocatorSourceKind.PERSISTED_ROW,
        binding_table="sample_rows",
        hash_field="published_artifact_envelope_hash",
        path_field="target_content_addressed_path",
    )
    assert projection_graph.locator == spec.ArtifactGraphLocatorSpec(
        kind=spec.ArtifactGraphLocatorSourceKind.CONTRACT_INPUT,
        contract_type="DataSnoopingEvidenceRef",
        hash_field="publication_projection_hash",
        path_field="publication_projection_path",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope_graph.verifier_code = "C9_BYPASS"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "bad_value", "message"),
    (
        ("kind", "RAW_SQL", "ArtifactGraphDerivationKind"),
        ("canonical_encoding", "JSON", "CanonicalEncoding"),
        ("hash_algorithm", "MD5", "RowHashAlgorithm"),
        ("cardinality", "ZERO_OR_MORE", "RelatedLookupCardinality"),
        ("failure_policy", "IGNORE", "CanonicalJsonFailurePolicy"),
    ),
)
def test_artifact_graph_semantics_cannot_be_replaced_by_free_form_values(
    field_name: str,
    bad_value: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        dataclasses.replace(_artifact_graph_derivations()[0], **{field_name: bad_value})

    with pytest.raises(TypeError, match="ArtifactGraphLocatorSourceKind"):
        dataclasses.replace(
            _artifact_graph_derivations()[0].locator,
            kind="DIRECTORY_SCAN",
        )


def test_artifact_graph_derivation_rejects_ambiguous_or_unbound_metadata() -> None:
    envelope_graph, projection_graph = _artifact_graph_derivations()
    with pytest.raises(ValueError, match="artifact graph contract fields"):
        dataclasses.replace(
            envelope_graph.contract_surfaces[0],
            field_names=("record_type", "record_type"),
        )
    with pytest.raises(ValueError, match="contract types"):
        dataclasses.replace(
            envelope_graph,
            contract_surfaces=(*envelope_graph.contract_surfaces, envelope_graph.contract_surfaces[0]),
        )
    with pytest.raises(ValueError, match="must differ"):
        dataclasses.replace(
            envelope_graph.locator,
            path_field=envelope_graph.locator.hash_field,
        )
    with pytest.raises(ValueError, match="verifier code"):
        dataclasses.replace(envelope_graph, verifier_code="")
    with pytest.raises(ValueError, match="cannot name a binding table"):
        dataclasses.replace(projection_graph.locator, binding_table="sample_rows")

    missing_hash = dataclasses.replace(
        envelope_graph,
        locator=dataclasses.replace(envelope_graph.locator, hash_field="missing_hash"),
    )
    with pytest.raises(ValueError, match="hash field must exist"):
        spec.SchemaComponentSpec(
            component_id="C9_MISSING_HASH_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=_artifact_graph_coverage(missing_hash),
            artifact_graph_derivations=(missing_hash,),
        )
    non_text_path = dataclasses.replace(
        envelope_graph,
        locator=dataclasses.replace(envelope_graph.locator, path_field="sequence_no"),
    )
    with pytest.raises(ValueError, match="path field must be TEXT"):
        spec.SchemaComponentSpec(
            component_id="C9_NON_TEXT_PATH_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=_artifact_graph_coverage(non_text_path),
            artifact_graph_derivations=(non_text_path,),
        )

    incomplete_coverage = _artifact_graph_coverage(envelope_graph, projection_graph)[:-1]
    with pytest.raises(ValueError, match="non-persisted coverage"):
        spec.SchemaComponentSpec(
            component_id="C9_INCOMPLETE_GRAPH_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=incomplete_coverage,
            artifact_graph_derivations=(envelope_graph, projection_graph),
        )


def test_artifact_graphs_reject_cross_locator_surfaces_and_receipt_row_hash_roots() -> None:
    envelope_graph, projection_graph = _artifact_graph_derivations()
    cross_locator_projection = dataclasses.replace(
        projection_graph,
        contract_surfaces=(envelope_graph.contract_surfaces[0],),
    )
    with pytest.raises(ValueError, match="artifact graph derivation fields"):
        spec.SchemaComponentSpec(
            component_id="C9_CROSS_LOCATOR_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=_artifact_graph_coverage(envelope_graph),
            artifact_graph_derivations=(envelope_graph, cross_locator_projection),
        )

    derived_projection_locator = dataclasses.replace(
        envelope_graph,
        contract_surfaces=(
            spec.ArtifactGraphContractSpec(
                contract_type="DataSnoopingEvidenceRef",
                field_names=("publication_projection_hash",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="contract-input artifact locators cannot be derived"):
        spec.SchemaComponentSpec(
            component_id="C9_DERIVED_LOCATOR_CYCLE_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=_artifact_graph_coverage(derived_projection_locator, projection_graph),
            artifact_graph_derivations=(derived_projection_locator, projection_graph),
        )

    receipt_row_hash_projection = dataclasses.replace(
        projection_graph,
        locator=spec.ArtifactGraphLocatorSpec(
            kind=spec.ArtifactGraphLocatorSourceKind.PERSISTED_ROW,
            binding_table="sample_rows",
            hash_field="record_hash",
            path_field="target_content_addressed_path",
        ),
    )
    with pytest.raises(ValueError, match="cannot use an authority row record hash"):
        spec.SchemaComponentSpec(
            component_id="C9_RECEIPT_HASH_ROOT_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=_artifact_graph_coverage(envelope_graph, receipt_row_hash_projection),
            artifact_graph_derivations=(envelope_graph, receipt_row_hash_projection),
        )


def test_artifact_graph_hash_and_path_columns_cannot_pose_as_json_payloads() -> None:
    envelope_graph, projection_graph = _artifact_graph_derivations()
    fake_payload = spec.ContractFieldCoverageSpec(
        contract_type="FakeArtifact",
        field_name="payload",
        kind=spec.CoverageKind.CANONICAL_PAYLOAD,
        table_name="sample_rows",
        target_field=envelope_graph.locator.hash_field,
    )
    with pytest.raises(ValueError, match="artifact locator columns cannot be canonical payloads"):
        spec.SchemaComponentSpec(
            component_id="C9_FAKE_PAYLOAD_SAMPLE_V1",
            capabilities=("artifact.publication.exact_byte_graph",),
            tables=(_artifact_graph_table_spec(),),
            coverage=(*_artifact_graph_coverage(envelope_graph, projection_graph), fake_payload),
            artifact_graph_derivations=(envelope_graph, projection_graph),
        )


def test_relation_aggregate_expression_is_closed_and_fail_closed() -> None:
    bindings = (
        spec.LookupBinding(
            "family_id",
            spec.ColumnValue("family_id", spec.RowSource.NEW),
        ),
    )
    expected = spec.RelatedValueLookupSpec(
        table="evaluation_batches",
        bindings=(
            spec.LookupBinding(
                "record_hash",
                spec.ColumnValue("batch_record_hash", spec.RowSource.NEW),
            ),
        ),
        value="candidate_count",
    )
    count = spec.RelationAggregateExpression(
        table="candidate_evaluations",
        bindings=bindings,
        aggregate=spec.RelationAggregateKind.COUNT_ROWS,
        source=spec.RowSource.RELATED,
        value=None,
        order=(),
        operator=spec.ComparisonOperator.NE,
        expected=expected,
    )
    ordered_hash = spec.RelationAggregateExpression(
        table="candidate_receipts",
        bindings=bindings,
        aggregate=spec.RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
        source=spec.RowSource.RELATED,
        value="receipt_hash",
        order=(spec.RelationAggregateOrderSpec("config_hash"),),
        operator=spec.ComparisonOperator.NE,
        expected=spec.LiteralValue("a" * 64),
        predicate=spec.ComparisonExpression(
            spec.ColumnValue("status", spec.RowSource.RELATED),
            spec.ComparisonOperator.EQ,
            spec.LiteralValue("COMPLETE"),
        ),
    )

    assert count.expected.cardinality is spec.RelatedLookupCardinality.EXACTLY_ONE
    assert count.expected.key_requirement is spec.RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    direct_count = dataclasses.replace(
        count,
        expected=spec.ColumnValue("candidate_count", spec.RowSource.NEW),
    )
    assert direct_count.expected == spec.ColumnValue("candidate_count", spec.RowSource.NEW)
    assert ordered_hash.order[0].direction is spec.SortDirection.ASC
    assert ordered_hash.order[0].collation is spec.Collation.BINARY

    with pytest.raises(ValueError, match="RELATED rows"):
        dataclasses.replace(count, source=spec.RowSource.NEW)
    with pytest.raises(ValueError, match="forbids value"):
        dataclasses.replace(count, value="config_hash")
    with pytest.raises(ValueError, match="non-negative"):
        dataclasses.replace(count, expected=spec.LiteralValue(-1))
    with pytest.raises(ValueError, match="NEW or CURRENT source"):
        dataclasses.replace(
            count,
            expected=spec.ColumnValue("candidate_count", spec.RowSource.RELATED),
        )
    with pytest.raises(ValueError, match="requires a value"):
        dataclasses.replace(
            count,
            aggregate=spec.RelationAggregateKind.COUNT_DISTINCT,
        )
    with pytest.raises(ValueError, match="BINARY ASC"):
        dataclasses.replace(
            ordered_hash,
            order=(
                spec.RelationAggregateOrderSpec(
                    "config_hash",
                    direction=spec.SortDirection.DESC,
                ),
            ),
        )
    with pytest.raises(ValueError, match="SHA-256"):
        dataclasses.replace(ordered_hash, expected=spec.LiteralValue("not-a-hash"))


def test_relation_aggregate_rejects_recursive_or_ambiguous_lookups() -> None:
    binding = spec.LookupBinding(
        "family_id",
        spec.ColumnValue("family_id", spec.RowSource.NEW),
    )
    related = spec.RelationExistsExpression("families", (binding,))
    with pytest.raises(ValueError, match="nested relation"):
        spec.RelationAggregateExpression(
            table="candidate_evaluations",
            bindings=(binding,),
            aggregate=spec.RelationAggregateKind.COUNT_ROWS,
            source=spec.RowSource.RELATED,
            value=None,
            order=(),
            operator=spec.ComparisonOperator.NE,
            expected=spec.LiteralValue(1),
            predicate=related,
        )
    with pytest.raises(ValueError, match="unique"):
        spec.RelatedValueLookupSpec(
            table="evaluation_batches",
            bindings=(binding, binding),
            value="candidate_count",
        )
    with pytest.raises(ValueError, match="RELATED values"):
        spec.RelatedValueLookupSpec(
            table="evaluation_batches",
            bindings=(binding,),
            value="candidate_count",
            source=spec.RowSource.NEW,
        )
    literal_binding = spec.LookupBinding("family_id", spec.LiteralValue("family-1"))
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        spec.RelatedValueLookupSpec(
            table="evaluation_batches",
            bindings=(literal_binding,),
            value="candidate_count",
        )
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        spec.RelationAggregateExpression(
            table="candidate_evaluations",
            bindings=(literal_binding,),
            aggregate=spec.RelationAggregateKind.COUNT_ROWS,
            source=spec.RowSource.RELATED,
            value=None,
            order=(),
            operator=spec.ComparisonOperator.NE,
            expected=spec.LiteralValue(1),
        )
    with pytest.raises(ValueError, match="RELATED columns"):
        spec.RelationAggregateExpression(
            table="candidate_evaluations",
            bindings=(binding,),
            aggregate=spec.RelationAggregateKind.COUNT_ROWS,
            source=spec.RowSource.RELATED,
            value=None,
            order=(),
            operator=spec.ComparisonOperator.NE,
            expected=spec.LiteralValue(1),
            predicate=spec.ComparisonExpression(
                spec.ColumnValue("status", spec.RowSource.NEW),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("COMPLETE"),
            ),
        )


def test_join_aggregate_is_closed_exact_and_alias_scoped() -> None:
    terminal_join = spec.RelationJoinSpec(
        table="search_attempt_events",
        alias="terminal_event",
        on=(
            spec.RelationJoinEqualitySpec(
                "family_search",
                "search_id",
                "terminal_event",
                "search_id",
            ),
        ),
        predicate=spec.MembershipExpression(
            spec.QualifiedColumnValue("terminal_event", "status"),
            (spec.LiteralValue("FAILED"), spec.LiteralValue("COMPLETED")),
        ),
    )
    aggregate = spec.RelationJoinAggregateExpression(
        anchor_table="data_snooping_family_searches",
        anchor_alias="family_search",
        bindings=(
            spec.LookupBinding(
                "family_id",
                spec.ColumnValue("family_id", spec.RowSource.NEW),
            ),
        ),
        joins=(terminal_join,),
        aggregate=spec.RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH,
        source=spec.RowSource.RELATED,
        value=spec.QualifiedColumnValue("terminal_event", "event_hash"),
        order=(
            spec.RelationJoinAggregateOrderSpec("family_search", "link_ordinal"),
            spec.RelationJoinAggregateOrderSpec("family_search", "search_id"),
        ),
        operator=spec.ComparisonOperator.NE,
        expected=spec.ColumnValue(
            "ordered_search_terminal_hashes_hash",
            spec.RowSource.NEW,
        ),
    )

    guard = spec.TriggerPolicySpec(
        name="family_freezes_terminal_searches",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="FREEZE_SEARCHES_TERMINAL",
        violation_when=aggregate,
    )
    assert guard.violation_when is aggregate
    assert terminal_join.cardinality is spec.RelationJoinCardinality.EXACTLY_ONE

    with pytest.raises(ValueError, match="right alias"):
        dataclasses.replace(
            terminal_join,
            on=(
                spec.RelationJoinEqualitySpec(
                    "family_search",
                    "search_id",
                    "different_event",
                    "search_id",
                ),
            ),
        )
    with pytest.raises(ValueError, match="already belong"):
        dataclasses.replace(
            aggregate,
            joins=(
                dataclasses.replace(
                    terminal_join,
                    on=(
                        spec.RelationJoinEqualitySpec(
                            "unknown_alias",
                            "search_id",
                            "terminal_event",
                            "search_id",
                        ),
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="BINARY ASC"):
        dataclasses.replace(
            aggregate,
            order=(
                spec.RelationJoinAggregateOrderSpec(
                    "family_search",
                    "link_ordinal",
                    direction=spec.SortDirection.DESC,
                ),
            ),
        )
    with pytest.raises(ValueError, match="unqualified join-predicate"):
        dataclasses.replace(
            terminal_join,
            predicate=spec.ComparisonExpression(
                spec.ColumnValue("status", spec.RowSource.RELATED),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("COMPLETED"),
            ),
        )


def test_qualified_columns_cannot_escape_join_aggregate_scope() -> None:
    qualified_predicate = spec.ComparisonExpression(
        spec.QualifiedColumnValue("terminal_event", "status"),
        spec.ComparisonOperator.EQ,
        spec.LiteralValue("COMPLETED"),
    )
    binding = spec.LookupBinding(
        "family_id",
        spec.ColumnValue("family_id", spec.RowSource.NEW),
    )

    with pytest.raises(ValueError, match="qualified relation columns"):
        spec.CheckConstraintSpec("qualified_escape", qualified_predicate)
    with pytest.raises(ValueError, match="qualified columns"):
        spec.RelationAggregateExpression(
            table="search_attempt_events",
            bindings=(binding,),
            aggregate=spec.RelationAggregateKind.COUNT_ROWS,
            source=spec.RowSource.RELATED,
            value=None,
            order=(),
            operator=spec.ComparisonOperator.NE,
            expected=spec.LiteralValue(1),
            predicate=qualified_predicate,
        )
    with pytest.raises(ValueError, match="qualified columns"):
        spec.RelationExistsExpression(
            table="search_attempt_events",
            bindings=(binding,),
            predicate=qualified_predicate,
        )


def test_canonical_json_field_is_typed_and_fail_closed() -> None:
    field = spec.CanonicalJsonFieldValue(
        document=spec.ColumnValue("trial_proof_json", spec.RowSource.NEW),
        path=("raw_proposal_count",),
        scalar_type=spec.CanonicalJsonScalarType.INTEGER,
    )

    assert field.require_canonical_json is True
    assert field.failure_policy is spec.CanonicalJsonFailurePolicy.REJECT
    with pytest.raises(ValueError, match="CURRENT, NEW, or RELATED"):
        dataclasses.replace(
            field,
            document=spec.ColumnValue("trial_proof_json", spec.RowSource.OLD),
        )
    with pytest.raises(ValueError, match="must not be empty"):
        dataclasses.replace(field, path=())
    with pytest.raises(ValueError, match="path item"):
        dataclasses.replace(field, path=("raw-proposal-count",))
    with pytest.raises(TypeError, match="scalar type"):
        dataclasses.replace(field, scalar_type="INTEGER")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-canonical"):
        dataclasses.replace(field, require_canonical_json=False)

    with pytest.raises(ValueError, match="CURRENT"):
        spec.CheckConstraintSpec(
            "json_new_row_escape",
            spec.ComparisonExpression(
                field,
                spec.ComparisonOperator.GE,
                spec.LiteralValue(0),
            ),
        )


def test_insert_guards_reject_free_qualified_columns_and_lookup_binding_escapes() -> None:
    free_qualified = spec.ComparisonExpression(
        spec.QualifiedColumnValue("unbound_relation", "state"),
        spec.ComparisonOperator.EQ,
        spec.LiteralValue("CLOSED"),
    )
    with pytest.raises(ValueError, match="qualified"):
        spec.TriggerPolicySpec(
            name="unbound_qualified_insert_guard",
            kind=spec.TriggerPolicyKind.INSERT_GUARD,
            event=spec.TriggerEvent.INSERT,
            error_code="UNBOUND_RELATED_COLUMN",
            violation_when=free_qualified,
        )

    with pytest.raises(ValueError, match="qualified RELATED"):
        spec.LookupBinding(
            "family_id",
            spec.QualifiedColumnValue("families", "family_id"),
        )

    related_json = spec.CanonicalJsonFieldValue(
        document=spec.ColumnValue("payload_json", spec.RowSource.RELATED),
        path=("family_id",),
        scalar_type=spec.CanonicalJsonScalarType.TEXT,
    )
    with pytest.raises(ValueError, match="RELATED JSON"):
        spec.LookupBinding("family_id", related_json)


def test_canonical_json_payload_hash_is_closed_frozen_and_fail_closed() -> None:
    payload_hash = spec.CanonicalJsonPayloadHashExpression(
        document=spec.ColumnValue("trial_proof_json", spec.RowSource.NEW),
        excluded_paths=(("proof_hash",),),
        encoding=spec.CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1,
        algorithm=spec.RowHashAlgorithm.SHA256,
        operator=spec.ComparisonOperator.NE,
        expected_hash=spec.ColumnValue("proof_hash", spec.RowSource.NEW),
    )

    assert payload_hash.require_canonical_json is True
    assert payload_hash.failure_policy is spec.CanonicalJsonFailurePolicy.REJECT
    assert payload_hash.encoding is spec.CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1
    assert payload_hash.algorithm is spec.RowHashAlgorithm.SHA256
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload_hash.operator = spec.ComparisonOperator.EQ  # type: ignore[misc]
    with pytest.raises(ValueError, match="CURRENT or NEW"):
        dataclasses.replace(
            payload_hash,
            document=spec.ColumnValue("trial_proof_json", spec.RowSource.RELATED),
        )
    with pytest.raises(ValueError, match="same row source"):
        dataclasses.replace(
            payload_hash,
            expected_hash=spec.ColumnValue("proof_hash", spec.RowSource.CURRENT),
        )
    exact_batch_bindings = (
        spec.LookupBinding(
            "evaluation_batch_id",
            spec.ColumnValue("evaluation_batch_id", spec.RowSource.NEW),
        ),
    )
    full_document_hash = dataclasses.replace(
        payload_hash,
        document=spec.ColumnValue("ordered_config_hashes_json", spec.RowSource.NEW),
        excluded_paths=(),
        expected_hash=spec.RelatedValueLookupSpec(
            table="data_snooping_evaluation_batches",
            bindings=exact_batch_bindings,
            value="ordered_candidate_config_hashes_hash",
        ),
    )
    assert full_document_hash.excluded_paths == ()
    assert isinstance(full_document_hash.expected_hash, spec.RelatedValueLookupSpec)
    with pytest.raises(TypeError, match="row column or EXACTLY_ONE lookup"):
        dataclasses.replace(payload_hash, expected_hash=spec.LiteralValue("f" * 64))
    with pytest.raises(ValueError, match="unique"):
        dataclasses.replace(
            payload_hash,
            excluded_paths=(("proof_hash",), ("proof_hash",)),
        )
    with pytest.raises(ValueError, match="EQ or NE"):
        dataclasses.replace(payload_hash, operator=spec.ComparisonOperator.GT)
    with pytest.raises(ValueError, match="non-canonical"):
        dataclasses.replace(payload_hash, require_canonical_json=False)

    guard = spec.TriggerPolicySpec(
        name="trial_proof_payload_hash_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="TRIAL_PROOF_HASH_MISMATCH",
        violation_when=payload_hash,
    )
    assert guard.violation_when is payload_hash
    with pytest.raises(TypeError, match="closed scalar"):
        spec.ComparisonExpression(
            payload_hash,  # type: ignore[arg-type]
            spec.ComparisonOperator.EQ,
            spec.LiteralValue(1),
        )


def test_related_canonical_json_arrays_are_exact_typed_and_fail_closed() -> None:
    bindings = (
        spec.LookupBinding(
            "family_id",
            spec.ColumnValue("family_id", spec.RowSource.NEW),
        ),
    )
    element_at = spec.RelatedCanonicalJsonArrayValueSpec(
        table="data_snooping_declarations",
        bindings=bindings,
        column="replication_schedule_json",
        element_type=spec.CanonicalJsonScalarType.INTEGER,
        projection=spec.CanonicalJsonArrayProjection.ELEMENT_AT_ORDINAL,
        ordinal=spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        ordinal_offset=-1,
    )
    declared_domain = dataclasses.replace(
        element_at,
        column="block_length_grid_json",
        projection=spec.CanonicalJsonArrayProjection.CANONICAL_ARRAY,
        ordinal=None,
        ordinal_offset=0,
    )
    contains = spec.RelatedCanonicalJsonArrayContainsExpression(
        table="data_snooping_declarations",
        bindings=bindings,
        column="block_length_grid_json",
        element_type=spec.CanonicalJsonScalarType.INTEGER,
        element=spec.ColumnValue("block_length", spec.RowSource.NEW),
        negated=True,
    )
    array_length = spec.RelatedCanonicalJsonArrayLengthValueSpec(
        table="data_snooping_declarations",
        bindings=bindings,
        column="replication_schedule_json",
        element_type=spec.CanonicalJsonScalarType.INTEGER,
    )

    for expression in (element_at, declared_domain, contains, array_length):
        assert expression.source is spec.RowSource.RELATED
        assert expression.cardinality is spec.RelatedLookupCardinality.EXACTLY_ONE
        assert expression.key_requirement is spec.RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
        assert expression.require_canonical_json is True
        assert expression.failure_policy is spec.CanonicalJsonFailurePolicy.REJECT
    with pytest.raises(ValueError, match="RELATED values"):
        dataclasses.replace(element_at, source=spec.RowSource.NEW)
    with pytest.raises((TypeError, ValueError), match="cardinality"):
        dataclasses.replace(element_at, cardinality="ZERO_OR_MORE")  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError), match="key requirement"):
        dataclasses.replace(element_at, key_requirement="ANY_INDEX")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical JSON"):
        dataclasses.replace(element_at, require_canonical_json=False)
    with pytest.raises(ValueError, match="canonical JSON"):
        dataclasses.replace(array_length, require_canonical_json=False)
    with pytest.raises(ValueError, match="requires a local ordinal"):
        dataclasses.replace(element_at, ordinal=None)
    with pytest.raises(ValueError, match="forbids ordinal"):
        dataclasses.replace(declared_domain, ordinal=spec.ColumnValue("schedule_ordinal"))
    assert dataclasses.replace(element_at, ordinal_offset=-2).ordinal_offset == -2
    with pytest.raises(ValueError, match="current offset -1 or predecessor offset -2"):
        dataclasses.replace(element_at, ordinal_offset=-3)
    with pytest.raises(ValueError, match="another relation"):
        dataclasses.replace(contains, element=element_at)
    with pytest.raises(ValueError, match="RELATED JSON"):
        dataclasses.replace(
            contains,
            element=spec.CanonicalJsonFieldValue(
                document=spec.ColumnValue("payload_json", spec.RowSource.RELATED),
                path=("block_length",),
                scalar_type=spec.CanonicalJsonScalarType.INTEGER,
            ),
        )

    guard = spec.TriggerPolicySpec(
        name="declared_block_length_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="UNDECLARED_BLOCK_LENGTH",
        violation_when=contains,
    )
    assert guard.violation_when is contains
    schedule_has_next = spec.ComparisonExpression(
        spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        spec.ComparisonOperator.LT,
        array_length,
    )
    assert schedule_has_next.right is array_length
    with pytest.raises(ValueError, match="domain values"):
        spec.ComparisonExpression(
            declared_domain,
            spec.ComparisonOperator.EQ,
            spec.LiteralValue("[]"),
        )


def test_ordinal_offset_is_a_closed_predecessor_value_only() -> None:
    predecessor = spec.OrdinalOffsetValue(
        spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        -1,
    )

    assert spec.LookupBinding("schedule_ordinal", predecessor).value is predecessor
    with pytest.raises(dataclasses.FrozenInstanceError):
        predecessor.offset = 0  # type: ignore[misc]
    with pytest.raises(ValueError, match="CURRENT or NEW"):
        dataclasses.replace(
            predecessor,
            value=spec.ColumnValue("schedule_ordinal", spec.RowSource.RELATED),
        )
    with pytest.raises(ValueError, match="predecessor offset -1"):
        dataclasses.replace(predecessor, offset=0)


def test_relation_arg_extremum_is_alias_scoped_and_metric_fixed() -> None:
    join = spec.RelationJoinSpec(
        table="family_benchmarks",
        alias="benchmark",
        on=(
            spec.RelationJoinEqualitySpec(
                "candidate",
                "batch_id",
                "benchmark",
                "batch_id",
            ),
        ),
    )
    metric = spec.RelationMetricSpec(
        metric_id="MEAN_NET_RETURN_DIFFERENTIAL_V1",
        inputs=(
            spec.QualifiedColumnValue("candidate", "series_payload_json"),
            spec.QualifiedColumnValue("benchmark", "series_payload_json"),
        ),
    )
    extremum = spec.RelationArgExtremumExpression(
        anchor_table="candidate_evaluations",
        anchor_alias="candidate",
        bindings=(
            spec.LookupBinding(
                "family_id",
                spec.ColumnValue("family_id", spec.RowSource.NEW),
            ),
        ),
        joins=(join,),
        metric=metric,
        extremum=spec.RelationExtremum.MAX,
        tie_break=(spec.RelationJoinAggregateOrderSpec("candidate", "config_hash"),),
        result=(
            spec.QualifiedColumnValue("candidate", "config_hash"),
            spec.QualifiedColumnValue("candidate", "evaluation_receipt_hash"),
        ),
        operator=spec.ComparisonOperator.NE,
        expected=(
            spec.ColumnValue("winner_config_hash", spec.RowSource.NEW),
            spec.ColumnValue("winner_evaluation_receipt_hash", spec.RowSource.NEW),
        ),
    )

    assert extremum.metric.metric_id == "MEAN_NET_RETURN_DIFFERENTIAL_V1"
    assert extremum.tie_break[0].direction is spec.SortDirection.ASC
    assert extremum.tie_break[0].collation is spec.Collation.BINARY
    with pytest.raises(ValueError, match="closed relation metric"):
        dataclasses.replace(metric, metric_id="CALLER_DEFINED_METRIC")
    with pytest.raises(ValueError, match="candidate and benchmark"):
        dataclasses.replace(metric, inputs=metric.inputs[:1])
    with pytest.raises(ValueError, match="series_payload_json"):
        dataclasses.replace(
            extremum,
            metric=dataclasses.replace(
                metric,
                inputs=(
                    spec.QualifiedColumnValue("candidate", "config_hash"),
                    metric.inputs[1],
                ),
            ),
        )
    with pytest.raises(ValueError, match="MAX"):
        dataclasses.replace(extremum, extremum=spec.RelationExtremum.MIN)
    with pytest.raises(ValueError, match="BINARY ASC"):
        dataclasses.replace(
            extremum,
            tie_break=(
                spec.RelationJoinAggregateOrderSpec(
                    "candidate",
                    "config_hash",
                    direction=spec.SortDirection.DESC,
                ),
            ),
        )
    with pytest.raises(ValueError, match="unknown alias"):
        dataclasses.replace(
            extremum,
            metric=dataclasses.replace(
                metric,
                inputs=(
                    metric.inputs[0],
                    spec.QualifiedColumnValue("unjoined", "mean_net_return"),
                ),
            ),
        )
    with pytest.raises(ValueError, match="config_hash"):
        dataclasses.replace(
            extremum,
            tie_break=(
                spec.RelationJoinAggregateOrderSpec(
                    "candidate",
                    "evaluation_receipt_hash",
                ),
            ),
        )
    with pytest.raises(ValueError, match="config and evaluation receipt"):
        dataclasses.replace(
            extremum,
            result=(
                spec.QualifiedColumnValue("benchmark", "config_hash"),
                extremum.result[1],
            ),
        )
    with pytest.raises(ValueError, match="already belong"):
        dataclasses.replace(
            extremum,
            joins=(
                dataclasses.replace(
                    join,
                    on=(
                        spec.RelationJoinEqualitySpec(
                            "unjoined",
                            "batch_id",
                            "benchmark",
                            "batch_id",
                        ),
                    ),
                ),
            ),
        )

    guard = spec.TriggerPolicySpec(
        name="deterministic_family_winner_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="WINNER_NOT_ARGMAX",
        violation_when=extremum,
    )
    assert guard.violation_when is extremum


def test_latest_per_domain_policy_fixes_family_conclusion_and_terminal_hashes() -> None:
    bindings = tuple(
        spec.LookupBinding(column, spec.ColumnValue(column, spec.RowSource.NEW))
        for column in (
            "authority_instance_id",
            "family_id",
            "algorithm_version",
        )
    )
    inputs = (
        "block_length",
        "replication_count",
        "reality_check_result_hash",
        "spa_result_hash",
        "grid_point_status",
        "grid_point_verdict",
        "record_hash",
    )
    declared_domain = spec.RelatedCanonicalJsonArrayValueSpec(
        table="data_snooping_declarations",
        bindings=bindings,
        column="block_length_grid_json",
        element_type=spec.CanonicalJsonScalarType.INTEGER,
        projection=spec.CanonicalJsonArrayProjection.CANONICAL_ARRAY,
    )
    policy = spec.RelationLatestPerDomainPolicyExpression(
        table="data_snooping_grid_assessments",
        bindings=bindings,
        policy_id="ANY_GRID_DISAGREEMENT_MAX_P_V1",
        source=spec.RowSource.RELATED,
        inputs=inputs,
        order=(spec.RelationAggregateOrderSpec("block_length"),),
        expected_verdict=spec.ColumnValue(
            "diagnostic_verdict",
            spec.RowSource.NEW,
        ),
        expected_ordered_record_hashes_hash=spec.ColumnValue(
            "ordered_latest_grid_assessment_hashes_hash",
            spec.RowSource.NEW,
        ),
        declared_domain=declared_domain,
        domain_column="block_length",
        latest_ordinal_column="schedule_ordinal",
        latest_cardinality=(
            spec.RelationLatestPerDomainCardinality.EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE
        ),
        negated=True,
    )

    assert policy.policy_id == "ANY_GRID_DISAGREEMENT_MAX_P_V1"
    assert policy.source is spec.RowSource.RELATED
    assert policy.order[0].direction is spec.SortDirection.ASC
    assert policy.order[0].collation is spec.Collation.BINARY
    assert policy.latest_cardinality is (
        spec.RelationLatestPerDomainCardinality.EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE
    )
    with pytest.raises(ValueError, match="closed assessment policy"):
        dataclasses.replace(policy, policy_id="CALLER_DEFINED_POLICY")
    with pytest.raises(ValueError, match="exact declared grid evidence tuple"):
        dataclasses.replace(policy, inputs=tuple(reversed(inputs)))
    with pytest.raises(ValueError, match="RELATED rows"):
        dataclasses.replace(policy, source=spec.RowSource.NEW)
    with pytest.raises(ValueError, match="BINARY ASC"):
        dataclasses.replace(
            policy,
            order=(
                spec.RelationAggregateOrderSpec(
                    "block_length",
                    direction=spec.SortDirection.DESC,
                ),
            ),
        )
    with pytest.raises(ValueError, match="authority, family, and algorithm"):
        dataclasses.replace(policy, bindings=policy.bindings[:-1])
    with pytest.raises(ValueError, match="block_length"):
        dataclasses.replace(policy, domain_column="config_hash")
    with pytest.raises(ValueError, match="schedule_ordinal"):
        dataclasses.replace(policy, latest_ordinal_column="replication_count")
    with pytest.raises(ValueError, match="diagnostic_verdict"):
        dataclasses.replace(
            policy,
            expected_verdict=spec.ColumnValue(
                "grid_point_verdict",
                spec.RowSource.NEW,
            ),
        )
    with pytest.raises(ValueError, match="ordered_latest_grid_assessment_hashes_hash"):
        dataclasses.replace(
            policy,
            expected_ordered_record_hashes_hash=spec.ColumnValue(
                "record_hash",
                spec.RowSource.NEW,
            ),
        )
    with pytest.raises((TypeError, ValueError), match="latest-per-domain cardinality"):
        dataclasses.replace(policy, latest_cardinality="CALLER_DEFINED")
    with pytest.raises(ValueError, match="CANONICAL_ARRAY"):
        dataclasses.replace(
            policy,
            declared_domain=dataclasses.replace(
                declared_domain,
                projection=spec.CanonicalJsonArrayProjection.ELEMENT_AT_ORDINAL,
                ordinal=spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
                ordinal_offset=-1,
            ),
        )
    guard = spec.TriggerPolicySpec(
        name="family_assessment_policy_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="ASSESSMENT_POLICY_MISMATCH",
        violation_when=policy,
    )
    assert guard.violation_when is policy


def test_prefix_stream_extension_fixes_algorithm_tuple_and_local_sources() -> None:
    family_binding = spec.LookupBinding(
        "family_id",
        spec.ColumnValue("family_id", spec.RowSource.NEW),
    )
    algorithms = (
        "bootstrap_seed",
        "bootstrap_algorithm_version",
        "prng_algorithm_version",
        "canonical_json_version",
        "block_length_policy_id",
    )
    extension = spec.PrefixStreamExtensionExpression(
        previous_table="data_snooping_grid_assessments",
        previous_bindings=(
            family_binding,
            spec.LookupBinding(
                "record_hash",
                spec.ColumnValue("prev_record_hash", spec.RowSource.NEW),
            ),
        ),
        previous_prefix_hash_column="prefix_index_stream_hash",
        previous_replication_count_column="replication_count",
        previous_period_count_column="period_count",
        declaration_table="data_snooping_declarations",
        declaration_bindings=(family_binding,),
        algorithm_columns=algorithms,
        current_prefix_hash=spec.ColumnValue(
            "prefix_index_stream_hash",
            spec.RowSource.NEW,
        ),
        current_replication_count=spec.ColumnValue(
            "replication_count",
            spec.RowSource.NEW,
        ),
        current_period_count=spec.ColumnValue(
            "period_count",
            spec.RowSource.NEW,
        ),
        block_length=spec.ColumnValue("block_length", spec.RowSource.NEW),
        current_ordinal=spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        genesis_hash=spec.ColumnValue("genesis_record_hash", spec.RowSource.NEW),
        negated=True,
    )

    assert extension.algorithm_columns == algorithms
    assert extension.first_ordinal == 1
    assert (
        extension.predecessor_cardinality
        is spec.PrefixPredecessorCardinality.ZERO_AT_GENESIS_ELSE_EXACTLY_ONE
    )
    assert extension.genesis_empty_prefix is True
    with pytest.raises(ValueError, match="exact declared bootstrap algorithm tuple"):
        dataclasses.replace(extension, algorithm_columns=algorithms[:-1])
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        dataclasses.replace(
            extension,
            current_prefix_hash=spec.ColumnValue(
                "prefix_index_stream_hash",
                spec.RowSource.RELATED,
            ),
        )
    with pytest.raises(ValueError, match="first ordinal"):
        dataclasses.replace(extension, first_ordinal=0)
    with pytest.raises(ValueError, match="empty genesis stream"):
        dataclasses.replace(extension, genesis_empty_prefix=False)
    literal_binding = spec.LookupBinding("family_id", spec.LiteralValue("family-1"))
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        dataclasses.replace(extension, previous_bindings=(literal_binding,))
    with pytest.raises(TypeError, match="closed scalar"):
        spec.ComparisonExpression(
            extension,  # type: ignore[arg-type]
            spec.ComparisonOperator.EQ,
            spec.LiteralValue(1),
        )

    guard = spec.TriggerPolicySpec(
        name="bootstrap_prefix_extension_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="PREFIX_NOT_EXACT_EXTENSION",
        violation_when=extension,
    )
    assert guard.violation_when is extension


def test_chain_predecessor_is_exact_local_and_fail_closed() -> None:
    chain = spec.ChainPredecessorExpression(
        table="data_snooping_grid_assessments",
        partition_bindings=(
            spec.LookupBinding(
                "family_id",
                spec.ColumnValue("family_id", spec.RowSource.NEW),
            ),
            spec.LookupBinding(
                "block_length",
                spec.ColumnValue("block_length", spec.RowSource.NEW),
            ),
        ),
        ordinal_column="schedule_ordinal",
        current_ordinal=spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        record_hash_column="record_hash",
        current_previous_hash=spec.ColumnValue("prev_record_hash", spec.RowSource.NEW),
        genesis_hash=spec.ColumnValue("genesis_record_hash", spec.RowSource.NEW),
        negated=True,
    )

    with pytest.raises(ValueError, match="NEW or CURRENT"):
        dataclasses.replace(
            chain,
            current_previous_hash=spec.ColumnValue(
                "prev_record_hash",
                spec.RowSource.RELATED,
            ),
        )
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        dataclasses.replace(
            chain,
            partition_bindings=(spec.LookupBinding("family_id", spec.LiteralValue("family-1")),),
        )
    with pytest.raises(ValueError, match="row column"):
        dataclasses.replace(
            chain,
            current_ordinal=spec.OrdinalOffsetValue(
                spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
                -1,
            ),
        )

    guard = spec.TriggerPolicySpec(
        name="assessment_chain_predecessor_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="ASSESSMENT_CHAIN_GAP",
        violation_when=chain,
    )
    assert guard.violation_when is chain


def test_relation_chain_prefix_proves_exact_ancestor_to_current_tip() -> None:
    partition = (
        spec.LookupBinding(
            "authority_instance_id",
            spec.ColumnValue("authority_instance_id", spec.RowSource.NEW),
        ),
    )
    declaration = (
        spec.LookupBinding(
            "declaration_id",
            spec.ColumnValue("declaration_id", spec.RowSource.NEW),
        ),
    )
    prefix = spec.RelationChainPrefixExpression(
        table="research_authority_events",
        partition_bindings=partition,
        ordinal_column="event_ordinal",
        record_hash_column="record_hash",
        previous_hash_column="prev_record_hash",
        ancestor_hash=spec.RelatedValueLookupSpec(
            table="data_snooping_declarations",
            bindings=declaration,
            value="selection_scope_predecessor_head",
        ),
        head_hash=spec.ColumnValue(
            "selection_scope_history_head",
            spec.RowSource.NEW,
        ),
        source=spec.RowSource.RELATED,
        proof=spec.RelationChainPrefixProof.COMPLETE_ANCESTOR_TO_CURRENT_TIP,
        negated=True,
    )
    assert prefix.proof is spec.RelationChainPrefixProof.COMPLETE_ANCESTOR_TO_CURRENT_TIP
    with pytest.raises(ValueError, match="RELATED"):
        dataclasses.replace(prefix, source=spec.RowSource.NEW)
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        dataclasses.replace(
            prefix,
            head_hash=spec.ColumnValue(
                "selection_scope_history_head",
                spec.RowSource.RELATED,
            ),
        )
    with pytest.raises((TypeError, ValueError), match="chain-prefix proof"):
        dataclasses.replace(prefix, proof="CALLER_DEFINED")
    with pytest.raises(TypeError, match="EXACTLY_ONE lookup"):
        dataclasses.replace(
            prefix,
            ancestor_hash=spec.ColumnValue(
                "selection_scope_predecessor_head",
                spec.RowSource.NEW,
            ),
        )

    guard = spec.TriggerPolicySpec(
        name="selection_scope_chain_prefix_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="SCOPE_HISTORY_NOT_CURRENT_PREFIX",
        violation_when=prefix,
    )
    assert guard.violation_when is prefix


def test_relation_set_equality_is_complete_ordered_and_non_recursive() -> None:
    family_binding = spec.LookupBinding(
        "family_id",
        spec.ColumnValue("family_id", spec.RowSource.NEW),
    )
    previous_ordinal = spec.OrdinalOffsetValue(
        spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        -1,
    )
    previous_replication_count = spec.RelatedCanonicalJsonArrayValueSpec(
        table="data_snooping_declarations",
        bindings=(family_binding,),
        column="replication_schedule_json",
        element_type=spec.CanonicalJsonScalarType.INTEGER,
        projection=spec.CanonicalJsonArrayProjection.ELEMENT_AT_ORDINAL,
        ordinal=spec.ColumnValue("schedule_ordinal", spec.RowSource.NEW),
        ordinal_offset=-1,
    )
    related_ambiguous = spec.LogicalExpression(
        spec.LogicalOperator.AND,
        (
            spec.ComparisonExpression(
                spec.ColumnValue("grid_point_status", spec.RowSource.RELATED),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("AMBIGUOUS"),
            ),
            spec.ComparisonExpression(
                spec.ColumnValue("is_effective", spec.RowSource.RELATED),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue(0),
            ),
        ),
    )
    set_equality = spec.RelationSetEqualityExpression(
        left_table="data_snooping_assessments",
        left_bindings=(
            family_binding,
            spec.LookupBinding("schedule_ordinal", previous_ordinal),
            spec.LookupBinding("replication_count", previous_replication_count),
        ),
        left_value="block_length",
        left_predicate=related_ambiguous,
        left_include_values=(spec.ColumnValue("block_length", spec.RowSource.NEW),),
        right_table="data_snooping_declarations",
        right_bindings=(family_binding,),
        right_value="block_length",
        right_predicate=None,
        order=(spec.RelationAggregateOrderSpec("block_length"),),
        negated=True,
    )

    assert set_equality.order[0].direction is spec.SortDirection.ASC
    assert set_equality.order[0].collation is spec.Collation.BINARY
    assert previous_replication_count.cardinality is spec.RelatedLookupCardinality.EXACTLY_ONE
    with pytest.raises(ValueError, match="BINARY ASC"):
        dataclasses.replace(
            set_equality,
            order=(
                spec.RelationAggregateOrderSpec(
                    "block_length",
                    collation=spec.Collation.NOCASE,
                ),
            ),
        )
    with pytest.raises(ValueError, match="RELATED columns only"):
        dataclasses.replace(
            set_equality,
            left_predicate=spec.ComparisonExpression(
                spec.ColumnValue("grid_point_status", spec.RowSource.NEW),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("AMBIGUOUS"),
            ),
        )
    with pytest.raises(ValueError, match="NEW or CURRENT"):
        dataclasses.replace(
            set_equality,
            left_include_values=(spec.ColumnValue("block_length", spec.RowSource.RELATED),),
        )
    with pytest.raises(ValueError, match="nested relation"):
        spec.RelationExistsExpression(
            table="outer_rows",
            bindings=(family_binding,),
            predicate=set_equality,
        )

    guard = spec.TriggerPolicySpec(
        name="all_ambiguous_grid_points_advance_guard",
        kind=spec.TriggerPolicyKind.INSERT_GUARD,
        event=spec.TriggerEvent.INSERT,
        error_code="ASSESSMENT_GRID_SET_MISMATCH",
        violation_when=set_equality,
    )
    assert guard.violation_when is set_equality


def test_check_constraints_forbid_relational_or_non_current_row_escape_hatches() -> None:
    related = spec.RelationExistsExpression(
        table="other_rows",
        bindings=(spec.LookupBinding("family_id", spec.ColumnValue("family_id", spec.RowSource.CURRENT)),),
    )
    with pytest.raises(ValueError, match="relation lookups"):
        spec.CheckConstraintSpec("unsafe_related_check", related)
    with pytest.raises(ValueError, match="CURRENT"):
        spec.CheckConstraintSpec(
            "unsafe_new_row_check",
            spec.ComparisonExpression(
                spec.ColumnValue("state", spec.RowSource.NEW),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("OPEN"),
            ),
        )


def test_fk_index_trigger_hash_chain_and_table_policy_are_typed() -> None:
    table = _table_spec()

    assert table.primary_key_columns == ("family_id", "sequence_no")
    assert table.indexes[0].predicate is not None
    assert table.triggers[2].state_machine is not None
    assert table.chain is not None
    assert table.chain.record_hash_column == table.row_hash.record_hash_column
    assert table.verification_rules[1].referenced_table == "families"

    with pytest.raises(ValueError, match="same cardinality"):
        spec.ForeignKeySpec(
            columns=("family_id", "sequence_no"),
            referenced_table="families",
            referenced_columns=("family_id",),
        )
    with pytest.raises(ValueError, match="NO_UPDATE"):
        dataclasses.replace(table, triggers=table.triggers[1:])
    with pytest.raises(ValueError, match="same record-hash"):
        dataclasses.replace(
            table,
            chain=dataclasses.replace(table.chain, record_hash_column="state"),
        )
    with pytest.raises(ValueError, match="unknown column"):
        dataclasses.replace(
            table,
            indexes=(
                spec.IndexSpec(
                    "sample_rows_bad_index",
                    (spec.IndexColumnSpec("missing_column"),),
                ),
            ),
        )


def test_structural_verification_sources_resolve_exact_owned_schema_objects() -> None:
    table = _table_spec()

    valid_sources = (
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.ROW_HASH_SPEC,
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.ALL_FOREIGN_KEYS,
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.CHAIN_SPEC,
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.TERMINAL_HEAD,
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.PRIMARY_KEY,
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.UNIQUE_INDEX,
            source_id="sample_rows_one_closed",
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="sample_rows_state_check",
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="sample_rows_terminal_guard",
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
            columns=("family_id",),
            referenced_table="families",
            referenced_columns=("family_id",),
        ),
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.TRIGGER_POLICY,
            source_id="remote_rows_terminal_guard",
            source_table="remote_rows",
        ),
    )
    for ordinal, source in enumerate(valid_sources, start=1):
        dataclasses.replace(
            table,
            verification_rules=(
                spec.VerificationRuleSpec(
                    f"sample_structural_source_{ordinal}",
                    spec.VerificationRuleKind.FOREIGN_KEY,
                    structural_source=source,
                ),
            ),
        )

    invalid_sources = (
        (
            spec.StructuralVerificationSourceSpec(
                spec.StructuralVerificationSourceKind.UNIQUE_INDEX,
                source_id="missing_unique_index",
            ),
            "unique index",
        ),
        (
            spec.StructuralVerificationSourceSpec(
                spec.StructuralVerificationSourceKind.CHECK_CONSTRAINT,
                source_id="missing_check",
            ),
            "check constraint",
        ),
        (
            spec.StructuralVerificationSourceSpec(
                spec.StructuralVerificationSourceKind.TRIGGER_POLICY,
                source_id="missing_trigger",
            ),
            "trigger policy",
        ),
        (
            spec.StructuralVerificationSourceSpec(
                spec.StructuralVerificationSourceKind.TRIGGER_POLICY,
                source_id="sample_rows_no_update",
            ),
            "insert guard",
        ),
        (
            spec.StructuralVerificationSourceSpec(
                spec.StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
                columns=("family_id",),
                referenced_table="wrong_parent",
                referenced_columns=("family_id",),
            ),
            "exact foreign key",
        ),
    )
    for ordinal, (source, message) in enumerate(invalid_sources, start=1):
        with pytest.raises(ValueError, match=message):
            dataclasses.replace(
                table,
                verification_rules=(
                    spec.VerificationRuleSpec(
                        f"sample_invalid_structural_source_{ordinal}",
                        spec.VerificationRuleKind.FOREIGN_KEY,
                        structural_source=source,
                    ),
                ),
            )

    with pytest.raises(ValueError, match="chain source"):
        dataclasses.replace(
            table,
            chain=None,
            verification_rules=(
                spec.VerificationRuleSpec(
                    "sample_missing_chain_source",
                    spec.VerificationRuleKind.ORDERED_CHAIN,
                    structural_source=spec.StructuralVerificationSourceSpec(
                        spec.StructuralVerificationSourceKind.CHAIN_SPEC,
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="remote trigger"):
        spec.StructuralVerificationSourceSpec(
            spec.StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            source_id="sample_rows_state_check",
            source_table="remote_rows",
        )


def test_component_dependencies_and_contract_field_coverage_are_explicit() -> None:
    derivation = spec.ContractFieldDerivationSpec(
        derivation_id="derive.oos_terminal_state.v1",
        contract_type="OosRelease",
        field_name="is_terminal",
        kind=spec.ContractDerivationKind.IMMUTABLE_ROW_LOOKUP,
        binding_table="sample_rows",
        binding_field="state",
        source_table="sample_rows",
        source_key_field="state",
        source_value_field="record_hash",
    )
    required_surface = spec.RequiredContractSurfaceSpec(
        contract_type="OosRelease",
        field_names=("provider_authority_head",),
        provider_component_id="C6.external_authority",
        required_capability="external.authority_head",
    )
    component = spec.SchemaComponentSpec(
        component_id="C7.oos_authority",
        capabilities=("oos.release", "oos.receipt"),
        dependencies=(
            spec.ComponentDependencySpec(
                component_id="C4.selection_scope",
                kind=spec.DependencyKind.CROSS_TABLE_REFERENCE,
                required_capabilities=("selection.scope",),
            ),
        ),
        tables=(_table_spec(),),
        coverage=(
            spec.ContractFieldCoverageSpec(
                contract_type="OosRelease",
                field_name="family_id",
                kind=spec.CoverageKind.COLUMN,
                table_name="sample_rows",
                target_field="family_id",
            ),
            spec.ContractFieldCoverageSpec(
                contract_type="OosRelease",
                field_name="release_hash",
                kind=spec.CoverageKind.CANONICAL_PAYLOAD,
                table_name="sample_rows",
                target_field="record_hash",
            ),
            spec.ContractFieldCoverageSpec(
                contract_type="OosRelease",
                field_name="is_terminal",
                kind=spec.CoverageKind.NON_PERSISTED_DERIVATION,
                derivation_id="derive.oos_terminal_state.v1",
            ),
        ),
        contract_derivations=(derivation,),
        required_contract_surfaces=(required_surface,),
    )

    assert component.dependencies[0].required_capabilities == ("selection.scope",)
    assert tuple(item.kind for item in component.coverage) == (
        spec.CoverageKind.COLUMN,
        spec.CoverageKind.CANONICAL_PAYLOAD,
        spec.CoverageKind.NON_PERSISTED_DERIVATION,
    )
    assert component.contract_derivations == (derivation,)
    assert component.required_contract_surfaces == (required_surface,)
    with pytest.raises(ValueError, match="self-dependency"):
        dataclasses.replace(
            component,
            dependencies=(
                spec.ComponentDependencySpec(
                    component_id=component.component_id,
                    kind=spec.DependencyKind.CROSS_TABLE_REFERENCE,
                ),
            ),
        )
    with pytest.raises(ValueError, match="exactly one coverage"):
        dataclasses.replace(component, coverage=(component.coverage[0],) * 2)
    with pytest.raises(ValueError, match="derivation metadata"):
        dataclasses.replace(component, contract_derivations=())
    with pytest.raises(ValueError, match="required contract fields"):
        dataclasses.replace(
            component,
            required_contract_surfaces=(required_surface, required_surface),
        )
    with pytest.raises(ValueError, match="required contract surface provider"):
        dataclasses.replace(
            component,
            required_contract_surfaces=(
                dataclasses.replace(
                    required_surface,
                    provider_component_id=component.component_id,
                ),
            ),
        )
    with pytest.raises(ValueError, match="owned contract coverage"):
        dataclasses.replace(
            component,
            required_contract_surfaces=(
                dataclasses.replace(
                    required_surface,
                    field_names=("family_id",),
                ),
            ),
        )


def test_owned_table_external_authorities_require_exact_typed_provider_capabilities() -> None:
    external_rule = spec.VerificationRuleSpec(
        rule_id="sample_rows_actor_provenance",
        kind=spec.VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE,
        columns=("family_id",),
        external_reference_kind=spec.ExternalAuthorityReferenceKind.ACTOR_PROVENANCE,
        provider_contract_type="ActorProvenanceEvidence",
        provider_capability="actor_provenance_evidence.verify",
        external_bindings=(
            spec.ExternalFieldBindingSpec("family_id", "actor_id"),
        ),
        verifier_code="sample.verify_actor_provenance_v1",
    )
    table = dataclasses.replace(
        _table_spec(),
        verification_rules=(*_table_spec().verification_rules, external_rule),
    )
    requirement = spec.ExternalProviderCapabilityRequirementSpec(
        provider_contract_type="ActorProvenanceEvidence",
        provider_capability="actor_provenance_evidence.verify",
        resolution_stage=spec.ExternalProviderResolutionStage.C8_PASS_M,
    )
    profile = spec.ExternalAuthorityClaimProfileSpec(
        provider_contract_type="ActorProvenanceEvidence",
        provider_capability="actor_provenance_evidence.verify",
        external_reference_kind=spec.ExternalAuthorityReferenceKind.ACTOR_PROVENANCE,
        required_binding_columns=("family_id",),
        required_claims=(
            spec.ExternalProviderSemanticClaimSpec(
                claim_id="provenance_verified",
                provider_field_path="claims.provenance_verified",
                operator=spec.ExternalProviderClaimOperator.IS_TRUE,
                expected_value=True,
            ),
        ),
    )

    component = spec.SchemaComponentSpec(
        component_id="C4.actor_authority",
        capabilities=("actor.authority",),
        tables=(table,),
        required_external_provider_capabilities=(requirement,),
        external_authority_claim_profiles=(profile,),
    )
    assert component.required_external_provider_capabilities == (requirement,)
    assert component.external_authority_claim_profiles == (profile,)
    assert {
        item.value
        for item in spec.ExternalAuthorityReferenceKind
    }.issuperset({"MULTI_PARTY_GRANT", "ACTOR_PROVENANCE"})

    with pytest.raises(ValueError, match="exactly cover"):
        dataclasses.replace(component, required_external_provider_capabilities=())
    with pytest.raises(ValueError, match="exactly cover"):
        dataclasses.replace(
            component,
            required_external_provider_capabilities=(
                dataclasses.replace(
                    requirement,
                    provider_capability="actor_provenance_evidence.other",
                ),
            ),
        )
    with pytest.raises(ValueError, match="fail closed"):
        dataclasses.replace(profile, fail_closed=False)
    with pytest.raises(ValueError, match="binding columns"):
        dataclasses.replace(
            component,
            external_authority_claim_profiles=(
                dataclasses.replace(profile, required_binding_columns=("state",)),
            ),
        )


def test_components_describe_required_embedded_extensions_without_composing() -> None:
    required = spec.RequiredTableExtensionSpec(
        extension_id="C6.sample_rows.observed_pit",
        target_table="sample_rows",
        provider_component_id="C6.external_evidence",
        required_capability="evidence.observed_pit",
    )
    extension = spec.AuthorityTableExtensionSpec(
        extension_id=required.extension_id,
        target_table=required.target_table,
        column_insertion=spec.OrderedColumnInsertionSpec(
            position=spec.InsertionPosition.AFTER,
            anchor_column="state",
            columns=(spec.ColumnSpec("observed_pit_evidence_hash", spec.SqlType.TEXT, nullable=False),),
        ),
        row_hash_field_insertion=spec.OrderedFieldInsertionSpec(
            position=spec.InsertionPosition.AFTER,
            anchor_field="state",
            fields=("observed_pit_evidence_hash",),
        ),
        verification_rules=(
            spec.VerificationRuleSpec(
                "sample_rows_observed_pit_reference",
                spec.VerificationRuleKind.CROSS_TABLE_REFERENCE,
                columns=("observed_pit_evidence_hash",),
                referenced_table="observed_pit_authority",
            ),
        ),
    )
    core = spec.SchemaComponentSpec(
        component_id="C4.sample_core",
        capabilities=("sample.core",),
        tables=(_table_spec(),),
        required_extensions=(required,),
    )
    provider = spec.SchemaComponentSpec(
        component_id="C6.external_evidence",
        capabilities=("evidence.observed_pit",),
        dependencies=(
            spec.ComponentDependencySpec(
                component_id="C4.sample_core",
                kind=spec.DependencyKind.CONTRACT_LINEAGE,
                required_capabilities=("sample.core",),
            ),
        ),
        extensions=(extension,),
        coverage=(
            spec.ContractFieldCoverageSpec(
                contract_type="ObservedPitEvidence",
                field_name="evidence_hash",
                kind=spec.CoverageKind.COLUMN,
                table_name="sample_rows",
                target_field="observed_pit_evidence_hash",
            ),
        ),
    )

    assert core.required_extensions == (required,)
    assert provider.extensions == (extension,)
    assert extension.column_insertion.columns[0].name in (extension.row_hash_field_insertion.fields)
    assert not hasattr(spec, "compose_component_specs")
    assert not hasattr(spec, "seal_component_specs")

    with pytest.raises(ValueError, match="row-hash field additions"):
        dataclasses.replace(
            extension,
            row_hash_field_insertion=dataclasses.replace(
                extension.row_hash_field_insertion,
                fields=("different_field",),
            ),
        )


def test_extensions_are_additive_and_reject_ambiguous_insertion_claims() -> None:
    extension = spec.AuthorityTableExtensionSpec(
        extension_id="C6.sample_rows.observed_pit",
        target_table="sample_rows",
        column_insertion=spec.OrderedColumnInsertionSpec(
            spec.InsertionPosition.AFTER,
            "state",
            (spec.ColumnSpec("observed_pit_hash", spec.SqlType.TEXT, False),),
        ),
        row_hash_field_insertion=spec.OrderedFieldInsertionSpec(
            spec.InsertionPosition.AFTER,
            "state",
            ("observed_pit_hash",),
        ),
    )
    extension_fields = {field.name for field in dataclasses.fields(extension)}

    assert (
        not {
            "replace_columns",
            "remove_columns",
            "replace_triggers",
            "remove_triggers",
            "replace_row_hash",
        }
        & extension_fields
    )
    with pytest.raises(TypeError, match="unexpected keyword"):
        spec.AuthorityTableExtensionSpec(
            extension_id="C6.sample_rows.invalid_replace",
            target_table="sample_rows",
            replace_columns=("state",),  # type: ignore[call-arg]
        )
    with pytest.raises(ValueError, match="INSERT_GUARD"):
        dataclasses.replace(extension, triggers=(_immutable_triggers()[0],))
    with pytest.raises(ValueError, match="unique"):
        spec.OrderedColumnInsertionSpec(
            spec.InsertionPosition.AFTER,
            "state",
            (
                spec.ColumnSpec("duplicate_hash", spec.SqlType.TEXT),
                spec.ColumnSpec("duplicate_hash", spec.SqlType.TEXT),
            ),
        )

    same_anchor = dataclasses.replace(
        extension,
        extension_id="C7.sample_rows.release",
        column_insertion=spec.OrderedColumnInsertionSpec(
            spec.InsertionPosition.AFTER,
            "state",
            (spec.ColumnSpec("release_hash", spec.SqlType.TEXT, False),),
        ),
        row_hash_field_insertion=spec.OrderedFieldInsertionSpec(
            spec.InsertionPosition.AFTER,
            "state",
            ("release_hash",),
        ),
    )
    with pytest.raises(ValueError, match="insertion anchor"):
        spec.SchemaComponentSpec(
            component_id="C6.extensions",
            capabilities=("evidence.extensions",),
            extensions=(extension, same_anchor),
        )

    duplicate_field = dataclasses.replace(
        extension,
        extension_id="C7.sample_rows.duplicate_field",
        column_insertion=dataclasses.replace(
            extension.column_insertion,
            anchor_column="sequence_no",
        ),
        row_hash_field_insertion=dataclasses.replace(
            extension.row_hash_field_insertion,
            anchor_field="sequence_no",
        ),
    )
    with pytest.raises(ValueError, match="extension column"):
        spec.SchemaComponentSpec(
            component_id="C6.duplicate_fields",
            capabilities=("evidence.extensions",),
            extensions=(extension, duplicate_field),
        )


def test_renderer_contract_freezes_grammar_and_version_without_a_renderer() -> None:
    contract = spec.FAMILY_SCHEMA_RENDERER_GRAMMAR

    assert contract.schema_version == 3
    assert contract.ast_version == "QUANTOS_FAMILY_SCHEMA_AST_V1"
    assert contract.renderer_version == "QUANTOS_SQLITE_SCHEMA_RENDERER_V1"
    assert contract.keyword_case is spec.RendererKeywordCase.UPPER
    assert contract.identifier_quoting is spec.RendererIdentifierQuoting.DOUBLE_QUOTE
    assert contract.object_order is spec.RendererObjectOrder.COMPONENT_TABLE_INDEX_TRIGGER
    assert contract.allows_if_not_exists is False
    assert contract.allows_executescript is False
    assert contract.allows_transaction_control is False
    assert not any(name.startswith("render") for name in dir(spec))
    assert not hasattr(contract, "render")

    with pytest.raises(ValueError, match="schema_version"):
        dataclasses.replace(contract, schema_version=4)


def test_genesis_and_activation_contracts_bind_exact_historical_inputs() -> None:
    genesis = spec.FAMILY_AUTHORITY_GENESIS_CONTRACT
    activation = spec.FAMILY_SCHEMA_V3_ACTIVATION_CONTRACT
    inputs = spec.FamilySchemaActivationInputs(
        authority_instance_id="quantos-l2-research-01",
        activation_code_manifest_hash="a" * 64,
        activated_at_us=1_750_000_000_000_000,
    )

    assert genesis.table_name == "family_authority_genesis"
    assert genesis.singleton_key_value == 1
    assert genesis.record_type_literal == "FAMILY_AUTHORITY_GENESIS_V1"
    assert genesis.record_hash_spec.record_hash_column not in genesis.payload_fields
    assert genesis.origin_projection.value_tags == tuple(spec.SqliteValueTag)
    assert genesis.origin_projection.binding_lifetime is spec.OriginBindingLifetime.ACTIVATION_ONLY
    assert genesis.requires_expected_authority_instance_id is True
    assert genesis.claims_physical_clone_uniqueness is False
    assert inputs.activation_code_manifest_hash == "a" * 64

    assert activation.source_version == 2
    assert activation.target_version == 3
    assert activation.steps == tuple(spec.ActivationStep)
    assert activation.ddl_execution_policy is spec.DdlExecutionPolicy.INDIVIDUAL_STATEMENTS
    assert activation.commit_count == 1
    assert activation.rollback_count_on_failure == 1
    assert activation.source_verification.checks == tuple(spec.ActivationSourceCheck)
    assert spec.ActivationStep.FINAL_FAULT_CHECKPOINT in activation.kill_checkpoints

    with pytest.raises(ValueError, match="SHA-256"):
        dataclasses.replace(inputs, activation_code_manifest_hash="not-a-hash")
    with pytest.raises(ValueError, match="epoch-microsecond"):
        dataclasses.replace(inputs, activated_at_us=True)


def test_family_authority_genesis_table_spec_is_complete_renderable_and_immutable() -> None:
    genesis = spec.FAMILY_AUTHORITY_GENESIS_CONTRACT
    table = spec.FAMILY_AUTHORITY_GENESIS_TABLE_SPEC
    expected_payload_fields = (
        "singleton_key",
        "authority_instance_id",
        "v2_origin_fingerprint",
        "sealed_v3_manifest_hash",
        "activation_code_manifest_evidence_hash",
        "migration_id",
        "activated_at_us",
        "record_type",
    )
    expected_columns = (
        ("singleton_key", spec.SqlType.INTEGER, False),
        ("authority_instance_id", spec.SqlType.TEXT, False),
        ("v2_origin_fingerprint", spec.SqlType.TEXT, False),
        ("sealed_v3_manifest_hash", spec.SqlType.TEXT, False),
        ("activation_code_manifest_evidence_hash", spec.SqlType.TEXT, False),
        ("migration_id", spec.SqlType.TEXT, False),
        ("activated_at_us", spec.SqlType.INTEGER, False),
        ("record_type", spec.SqlType.TEXT, False),
        ("record_hash", spec.SqlType.TEXT, False),
    )
    expected_checks = (
        spec.CheckConstraintSpec(
            "family_authority_genesis_singleton_check",
            spec.ComparisonExpression(
                spec.ColumnValue("singleton_key"),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue(1),
            ),
        ),
        spec.CheckConstraintSpec(
            "family_authority_genesis_record_type_check",
            spec.ComparisonExpression(
                spec.ColumnValue("record_type"),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("FAMILY_AUTHORITY_GENESIS_V1"),
            ),
        ),
        spec.CheckConstraintSpec(
            "family_authority_genesis_migration_id_check",
            spec.ComparisonExpression(
                spec.ColumnValue("migration_id"),
                spec.ComparisonOperator.EQ,
                spec.LiteralValue("FAMILY_SCHEMA_V2_TO_V3_V1"),
            ),
        ),
        spec.CheckConstraintSpec(
            "family_authority_genesis_activated_at_us_positive_check",
            spec.ComparisonExpression(
                spec.ColumnValue("activated_at_us"),
                spec.ComparisonOperator.GT,
                spec.LiteralValue(0),
            ),
        ),
    )

    assert type(table) is spec.AuthorityTableSpec
    assert genesis.table_spec is table
    assert table.table_name == "family_authority_genesis"
    assert tuple((column.name, column.sql_type, column.nullable) for column in table.columns) == expected_columns
    assert table.primary_key_columns == ("singleton_key",)
    assert table.foreign_keys == ()
    assert table.indexes == (
        spec.IndexSpec(
            name="family_authority_genesis_lineage_unique",
            columns=(
                spec.IndexColumnSpec("authority_instance_id"),
                spec.IndexColumnSpec("record_hash"),
            ),
            unique=True,
        ),
    )
    assert table.checks == expected_checks
    assert genesis.payload_fields == expected_payload_fields
    assert genesis.record_hash_spec is table.row_hash
    assert table.row_hash.canonical_fields == expected_payload_fields
    assert table.row_hash.record_hash_column == "record_hash"
    assert table.row_hash.version_literal == "FAMILY_AUTHORITY_GENESIS_V1"
    assert table.row_hash.algorithm is spec.RowHashAlgorithm.SHA256
    assert table.row_hash.encoding is spec.CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1
    assert table.chain is None
    assert tuple(
        (
            trigger.name,
            trigger.kind,
            trigger.event,
            trigger.error_code,
            trigger.violation_when,
            trigger.state_machine,
        )
        for trigger in table.triggers
    ) == (
        (
            "family_authority_genesis_no_update",
            spec.TriggerPolicyKind.NO_UPDATE,
            spec.TriggerEvent.UPDATE,
            "FAMILY_AUTHORITY_GENESIS_IMMUTABLE",
            None,
            None,
        ),
        (
            "family_authority_genesis_no_delete",
            spec.TriggerPolicyKind.NO_DELETE,
            spec.TriggerEvent.DELETE,
            "FAMILY_AUTHORITY_GENESIS_IMMUTABLE",
            None,
            None,
        ),
    )
    assert tuple(
        (rule.rule_id, rule.kind, rule.columns, rule.verifier_code)
        for rule in table.verification_rules
    ) == (
        (
            "family_authority_genesis_row_hash",
            spec.VerificationRuleKind.ROW_HASH,
            expected_payload_fields,
            "family_schema.verify_genesis_row_hash_v1",
        ),
        (
            "family_authority_genesis_read",
            spec.VerificationRuleKind.GENESIS_BINDING,
            tuple(column[0] for column in expected_columns),
            "family_schema.verify_genesis_read_v1",
        ),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        table.table_name = "mutable_genesis"  # type: ignore[misc]
    with pytest.raises(ValueError, match="genesis table spec is fixed"):
        dataclasses.replace(
            genesis,
            table_spec=dataclasses.replace(table, checks=table.checks[:-1]),
        )
    with pytest.raises(ValueError, match="verification rules"):
        dataclasses.replace(table, verification_rules=())
    with pytest.raises(ValueError, match="NO_DELETE"):
        dataclasses.replace(table, triggers=table.triggers[:1])


def test_phase_a_version_permission_matrix_is_complete_and_fail_closed() -> None:
    contract = spec.PHASE_A_VERSION_PERMISSION_CONTRACT
    rules = {(rule.operation, rule.version_band): rule for rule in contract.rules}

    assert len(rules) == len(spec.VersionOperation) * len(spec.SchemaVersionBand)
    assert (
        rules[(spec.VersionOperation.PORTABLE_V2_INSPECTION, spec.SchemaVersionBand.FUTURE)].permission
        is spec.VersionPermission.READ_ONLY
    )
    assert (
        rules[(spec.VersionOperation.SEARCH_WRITE, spec.SchemaVersionBand.BELOW_V2)].permission
        is spec.VersionPermission.MIGRATE_SEARCH_TO_V2_THEN_REEVALUATE
    )
    assert rules[(spec.VersionOperation.SEARCH_WRITE, spec.SchemaVersionBand.EXACT_V3)].verifier_requirements == (
        spec.VerifierRequirement.EXACT_V2_SEARCH_WRITER,
        spec.VerifierRequirement.FULL_FAMILY_AUTHORITY,
        spec.VerifierRequirement.EXPECTED_AUTHORITY_INSTANCE_ID,
    )
    assert (
        rules[(spec.VersionOperation.FAMILY_ACTIVATE, spec.SchemaVersionBand.EXACT_V2)].permission
        is spec.VersionPermission.EXPLICIT_OFFLINE_ACTIVATION
    )
    assert (
        rules[(spec.VersionOperation.FAMILY_WRITE, spec.SchemaVersionBand.FUTURE)].permission
        is spec.VersionPermission.REJECT
    )
    assert contract.lifecycle_schema_exposed is False
    assert "LIFECYCLE" not in spec.VersionOperation.__members__


def test_module_has_no_sql_escape_hatch_and_only_pure_id_validation_surface() -> None:
    source = inspect.getsource(spec)
    public_functions = {
        name for name, value in vars(spec).items() if not name.startswith("_") and inspect.isfunction(value)
    }
    dataclass_fields = {
        field.name.lower()
        for value in vars(spec).values()
        if inspect.isclass(value) and dataclasses.is_dataclass(value)
        for field in dataclasses.fields(value)
    }

    assert public_functions == {"validate_authority_instance_id"}
    assert spec.validate_authority_instance_id("quantos-l2-research-01") == (
        "quantos-l2-research-01"
    )
    assert not ({"sql", "raw_sql", "ddl", "trigger_sql", "predicate_sql"} & dataclass_fields)
    assert "RawSql" not in source
    assert "sqlite3" not in source
    assert "FamilySchemaManifest" not in source
    assert not re.search(r"\bCREATE\s+(?:TABLE|INDEX|TRIGGER)\b", source, re.IGNORECASE)
    assert not re.search(r"\bBEGIN\s+IMMEDIATE\b", source, re.IGNORECASE)
    assert not any(name.startswith(("activate_family", "verify_family", "require_family")) for name in vars(spec))

import json
import os
from pathlib import Path

import pytest

from google.cloud.db_context_enrichment.main import (
    delete_context_set,
    generate_evalbench_configs,
    get_context_set,
    get_operation,
    list_context_set_locations,
    mutate_context_set,
    read_evaluation_result,
    upload_context_set,
    validate_context_set,
)


def test_mutate_context_set_success(tmp_path: Path):
    file_path = tmp_path / "context.json"
    mutations = [
        {
            "operation": "add",
            "type": "template",
            "value": {
                "nl_query": "Test query",
                "sql": "SELECT *",
                "intent": "Test intent",
                "manifest": "Test manifest",
                "parameterized": {
                    "parameterized_sql": "SELECT * FROM t",
                    "parameterized_intent": "Test",
                },
            },
        }
    ]

    result = mutate_context_set(str(file_path), json.dumps(mutations))

    assert "Successfully applied" in result
    assert file_path.exists()
    with open(file_path) as f:
        data = json.load(f)
    assert len(data.get("templates", [])) == 1
    assert data["templates"][0]["nl_query"] == "Test query"


def test_mutate_context_set_invalid_json(tmp_path: Path):
    file_path = tmp_path / "context.json"
    result = mutate_context_set(str(file_path), "invalid json")
    assert "Error applying mutations" in result
    assert "JSONDecodeError" in result or "invalid json" in result or "Error" in result


def test_mutate_context_set_non_list_json(tmp_path: Path):
    file_path = tmp_path / "context.json"
    result = mutate_context_set(
        str(file_path), json.dumps({"operation": "add", "type": "template"})
    )
    assert "must be a JSON list" in result


def test_mutate_context_set_validation_error(tmp_path: Path):
    file_path = tmp_path / "context.json"
    # Invalid operation
    mutations = [{"operation": "invalid", "type": "template"}]
    result = mutate_context_set(str(file_path), json.dumps(mutations))
    assert "Error applying mutations" in result


def test_validate_context_set_valid_file(tmp_path: Path):
    file_path = tmp_path / "context.json"
    file_path.write_text(
        json.dumps(
            {
                "value_searches": [
                    {
                        "query": "SELECT name FROM cities WHERE name = $value",
                        "concept_type": "City",
                    }
                ]
            }
        )
    )
    result = validate_context_set(str(file_path))
    parsed = json.loads(result)
    assert parsed["valid"] is True
    assert parsed["issues"] == []


def test_validate_context_set_reports_issues(tmp_path: Path):
    file_path = tmp_path / "context.json"
    file_path.write_text(
        json.dumps(
            {
                "value_searches": [
                    {"query": "SELECT 1", "concept_type": "City"},
                ]
            }
        )
    )
    result = validate_context_set(str(file_path))
    parsed = json.loads(result)
    assert parsed["valid"] is False
    assert any("$value" in i["message"] for i in parsed["issues"])


def test_validate_context_set_missing_file(tmp_path: Path):
    # End-to-end: wrapper returns a parseable JSON response for I/O errors
    # rather than raising. Error-case coverage lives in context_validator_test.
    result = validate_context_set(str(tmp_path / "missing.json"))
    parsed = json.loads(result)
    assert parsed["valid"] is False
    assert "missing.json" in parsed["issues"][0]["message"]


def test_generate_evalbench_configs_tool_bigtable(tmp_path: Path):
    golden_file = tmp_path / "golden.json"
    golden_file.write_text(
        json.dumps(
            [
                {
                    "id": "h1",
                    "database": "hotels",
                    "nlq": "Find all hotels",
                    "golden_sql": "SELECT * FROM `hotels`",
                }
            ]
        )
    )
    tools_file = tmp_path / "tools.yaml"
    tools_file.write_text(
        """kind: source
name: my-bigtable-source
type: bigtable
project: test-p
instance: test-i
"""
    )
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        out_dir = str(tmp_path / ".context-engineering" / "experiments" / "test_exp")
        result = generate_evalbench_configs(
            output_dir=out_dir,
            dataset_path=str(golden_file),
            context_set_id="test_ctx_id",
            toolbox_config_path=str(tools_file),
            toolbox_source_name="my-bigtable-source",
        )
        assert "Successfully generated all configs for evaluation" in result
        eval_dir = (
            tmp_path / ".context-engineering" / "experiments" / "test_exp" / "eval_configs"
        )
        assert (eval_dir / "db_config.yaml").exists()
        assert (eval_dir / "model_config.yaml").exists()
        assert (eval_dir / "run_config.yaml").exists()
        assert (eval_dir / "llmrater_config.yaml").exists()
        assert (eval_dir / "golden_queries.json").exists()
    finally:
        os.chdir(cwd)


@pytest.mark.asyncio
async def test_read_evaluation_result_tool(tmp_path: Path):
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text(
        "metric_name,metric_score,correct_results_count,total_results_count,job_id,run_time\n"
        "llmrater,100,5,5,run_001,2026-08-17 22:00:00\n"
    )
    (run_dir / "scores.csv").write_text(
        "comparator,comparison_error,comparison_logs,database,dialects,generated_error,generated_sql,id,job_id,score\n"
        "llmrater,,Skipped,hotels,['bigtable'],,,h1,run_001,100\n"
    )
    (run_dir / "evals.csv").write_text(
        "cleanup_sql,database,dialects,eval_query,eval_results,generated_error,generated_prompt,generated_result,generated_sql,golden_error,golden_eval_results,golden_result,golden_sql,id,job_id,nl_prompt,other,payload,prompt,prompt_generator_error,query_type,run_time,sanitized_sql,setup_sql,sql_generator_error,sql_generator_time,tags,trace_id\n"
    )
    report = await read_evaluation_result(str(run_dir))
    assert "Evaluation Summary" in report
    assert "No failure cases found (all passed)." in report


def test_upload_context_set_tool(monkeypatch, tmp_path: Path):
    called = {}

    def mock_upload(self, context_set, context_payload, description=""):
        called["context_set"] = context_set
        called["context_payload"] = context_payload
        called["description"] = description
        return {"operation": "op1"}

    monkeypatch.setattr(
        "google.cloud.db_context_enrichment.common.context_set_mcp_client.ContextSetMcpClient.upload_context_set",
        mock_upload,
    )
    # 1. Test direct context_payload
    res = upload_context_set(
        context_set="projects/p/locations/l/contextSets/c",
        context_payload='{"templates": []}',
        description="test desc",
    )
    assert json.loads(res) == {"operation": "op1"}
    assert called["context_set"] == "projects/p/locations/l/contextSets/c"
    assert called["description"] == "test desc"

    # 2. Test local_file_path
    ctx_file = tmp_path / "ctx.json"
    ctx_file.write_text('{"templates": []}')
    res2 = upload_context_set(
        context_set="projects/p/locations/l/contextSets/c",
        local_file_path=str(ctx_file),
    )
    assert json.loads(res2) == {"operation": "op1"}

    # 3. Test missing both raises ValueError
    with pytest.raises(ValueError, match="Either 'context_payload' or 'local_file_path'"):
        upload_context_set(context_set="projects/p/locations/l/contextSets/c")


def test_get_context_set_tool(monkeypatch):
    monkeypatch.setattr(
        "google.cloud.db_context_enrichment.common.context_set_mcp_client.ContextSetMcpClient.get_context_set",
        lambda self, cs: {"payload": "test_payload"},
    )
    res = get_context_set("projects/p/locations/l/contextSets/c")
    assert json.loads(res) == {"payload": "test_payload"}


def test_delete_context_set_tool(monkeypatch):
    monkeypatch.setattr(
        "google.cloud.db_context_enrichment.common.context_set_mcp_client.ContextSetMcpClient.delete_context_set",
        lambda self, cs: {"name": "projects/p/locations/l/operations/op1"},
    )
    res = delete_context_set("projects/p/locations/l/contextSets/c")
    assert json.loads(res) == {"name": "projects/p/locations/l/operations/op1"}


def test_list_context_set_locations_tool(monkeypatch):
    monkeypatch.setattr(
        "google.cloud.db_context_enrichment.common.context_set_mcp_client.ContextSetMcpClient.list_context_set_locations",
        lambda self, proj: ["us-central1"],
    )
    res = list_context_set_locations("my-project")
    assert json.loads(res) == ["us-central1"]


def test_get_operation_tool(monkeypatch):
    monkeypatch.setattr(
        "google.cloud.db_context_enrichment.common.context_set_mcp_client.ContextSetMcpClient.get_operation",
        lambda self, p, l, op: {
            "name": f"projects/{p}/locations/{l}/operations/{op}",
            "done": True,
        },
    )
    # Test passing full operation name
    res = get_operation(operation_name="projects/p/locations/l/operations/op123")
    assert json.loads(res)["done"] is True

    # Test passing explicit parameters
    res2 = get_operation(project_id="p", location="l", operation_id="op123")
    assert json.loads(res2)["done"] is True



"""Tracing: LangSmith's own env vars switch it on; LangChain and LangGraph trace themselves, no app code."""

import json
import time
import uuid

import httpx
import pytest
from conftest import _stop, run_agent, sign_up, start_api, unique_email
from fake_openai import reply_for

PASSWORD = "correct horse battery staple"


def traced(fake_langsmith: str, project: str) -> list[dict]:
    runs = httpx.get(f"{fake_langsmith}/received").json()["runs"]
    return [run for run in runs if run.get("session_name") == project]


def wait_for_finished_trace(fake_langsmith: str, project: str) -> list[dict]:
    """Runs are sent from a background thread, so poll until the root run has ended."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        runs = traced(fake_langsmith, project)
        if any(run.get("parent_run_id") is None and run.get("end_time") for run in runs):
            return runs
        time.sleep(0.2)
    raise AssertionError(f"no finished trace in {project}: {runs}")


def test_a_detective_run_is_traced_with_its_node_and_model_call(
    database_url: str, fake_openai: str, s3_endpoint: str, fake_langsmith: str
) -> None:
    project = f"test-{uuid.uuid4()}"
    proc, api = start_api(
        database_url,
        fake_openai,
        s3_endpoint,
        env={
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_ENDPOINT": fake_langsmith,
            "LANGSMITH_API_KEY": "lsv2-test-key",
            "LANGSMITH_PROJECT": project,
        },
    )
    try:
        cookie = sign_up(api, database_url, unique_email(), PASSWORD)
        case_id = httpx.get(f"{api}/cases/current", headers={"cookie": cookie}).json()["id"]
        run_agent(api, cookie, "My bike was stolen from the train station")
        runs = wait_for_finished_trace(fake_langsmith, project)
    finally:
        _stop(proc)

    assert httpx.get(f"{fake_langsmith}/received").json()["api_keys"] == ["lsv2-test-key"]
    by_id = {run["id"]: run for run in runs}
    [root] = [run for run in runs if run.get("parent_run_id") is None]
    assert "My bike was stolen from the train station" in json.dumps(root["inputs"])
    # LangGraph puts the thread id (our case id) in the trace metadata: LangSmith groups a case's runs by it.
    assert root["extra"]["metadata"]["thread_id"] == case_id

    # The loop (S12): decide asked the decision model (traced with @traceable), which chose "ask".
    [decide] = [run for run in runs if run["name"] == "decide"]
    [jev] = [run for run in runs if run["name"] == "decide-next-step"]
    assert by_id[jev["parent_run_id"]] is decide and jev["outputs"]["action"] == "ask"
    [node] = [run for run in runs if run["name"] == "ask" and run["run_type"] == "chain"]
    # The reply's model call sits under its reply node; the board's under update_board (S10).
    [llm] = [run for run in runs if run["run_type"] == "llm" and by_id[run["parent_run_id"]] is node]
    assert any(run["name"] == "update_board" and run["parent_run_id"] == root["id"] for run in runs)
    assert llm["extra"]["metadata"]["ls_model_name"] == "gpt-5.4-nano"
    assert "detective" in json.dumps(llm["inputs"]).lower()  # the system prompt the model got
    assert reply_for("My bike was stolen from the train station") in json.dumps(llm["outputs"])


def test_tests_never_trace_with_the_langsmith_settings_from_your_env(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    fake_openai: str,
    s3_endpoint: str,
    fake_langsmith: str,
) -> None:
    # As if your .env switched tracing on (pointed at the fake here, so a leak is visible and costs nothing).
    project = f"test-{uuid.uuid4()}"
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", fake_langsmith)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-your-real-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", project)
    proc, api = start_api(database_url, fake_openai, s3_endpoint)
    try:
        run_agent(api, sign_up(api, database_url, unique_email(), PASSWORD), "Who took my cat?")
    finally:
        _stop(proc)  # the SDK flushes pending traces on shutdown, so anything traced has arrived by now

    assert traced(fake_langsmith, project) == []


def test_indexing_evidence_is_traced_with_what_was_embedded(
    database_url: str, fake_openai: str, s3_endpoint: str, fake_langsmith: str
) -> None:
    project = f"test-{uuid.uuid4()}"
    proc, api = start_api(
        database_url,
        fake_openai,
        s3_endpoint,
        env={
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_ENDPOINT": fake_langsmith,
            "LANGSMITH_API_KEY": "lsv2-test-key",
            "LANGSMITH_PROJECT": project,
        },
    )
    try:
        cookie = sign_up(api, database_url, unique_email(), PASSWORD)
        note = httpx.post(
            f"{api}/cases/current/evidence/files",
            files={"file": ("note.txt", b"The umbrella was green.", "text/plain")},
            headers={"cookie": cookie},
        ).json()
        deadline = time.monotonic() + 15
        # A run arrives in two parts (start, then end with outputs): wait for the ended one.
        while not (
            runs := [
                r
                for r in traced(fake_langsmith, project)
                if r["name"] == "index-evidence" and r.get("end_time")
            ]
        ):
            assert time.monotonic() < deadline, "no index-evidence run"
            time.sleep(0.2)
    finally:
        _stop(proc)

    [run] = runs
    assert run["inputs"] == {
        "evidence": "note.txt",
        "evidence_id": note["id"],
        "case_id": run["inputs"]["case_id"],
    }
    assert run["outputs"]["count"] == 2
    assert [(d["kind"], d["text"]) for d in run["outputs"]["documents"]] == [
        ("fact", 'The document says: The umbrella was green. "The umbrella was green."'),
        ("chunk", "The umbrella was green."),
    ]

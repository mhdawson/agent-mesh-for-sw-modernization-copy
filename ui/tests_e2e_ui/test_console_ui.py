"""Browser smoke checks for the deployed Code Understanding console."""

import io
import json
import tarfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

QUERY_TIMEOUT_MS = 30 * 60 * 1000
PIPELINE_TIMEOUT_MS = 60 * 60 * 1000
INDEX_TRANSFER_TIMEOUT_MS = 10 * 60 * 1000
SOURCE_INDEX_NAME = "tic-tac-toe-sample"
ANALYSIS_REPO_NAME = "yappb"
ANALYSIS_REPO_BRANCH = "master"


def copy_bundle_with_new_slug(source: Path, destination: Path, new_slug: str) -> None:
    """Copy an index archive, changing only the bundle's display slug."""
    with tarfile.open(source, mode="r:gz") as original:
        with tarfile.open(destination, mode="w:gz") as copied:
            for member in original.getmembers():
                if member.name == "manifest.json":
                    manifest_file = original.extractfile(member)
                    assert manifest_file is not None, "Downloaded index has no readable manifest"
                    with manifest_file:
                        manifest = json.load(manifest_file)
                    manifest["git_slug"] = new_slug
                    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
                    member.size = len(manifest_bytes)
                    copied.addfile(member, io.BytesIO(manifest_bytes))
                elif member.isfile():
                    member_file = original.extractfile(member)
                    assert (
                        member_file is not None
                    ), f"Downloaded index member is unreadable: {member.name}"
                    with member_file:
                        copied.addfile(member, member_file)
                else:
                    copied.addfile(member)


def test_console_loads_repositories_and_recent_runs(page: Page) -> None:
    checkboxes = page.locator("#repo-list input[type=checkbox]")
    expect(checkboxes.first).to_be_visible()

    # Recent Runs renders either run cards or its normal empty state.
    runs = page.locator("#runs")
    expect(runs).to_be_visible()
    expect(runs).not_to_be_empty()


def test_tabs_and_repository_selection_work(page: Page) -> None:
    repositories_tab = page.locator('.cu-tabs button[data-tab="repos"]')
    analysis_tab = page.locator('.cu-tabs button[data-tab="pipelines"]')
    chat_tab = page.locator('.cu-tabs button[data-tab="chat"]')

    expect(page.locator("#tab-repos")).to_be_visible()
    analysis_tab.click()
    expect(page.locator("#tab-pipelines")).to_be_visible()
    expect(page.get_by_role("button", name="Run Analysis")).to_be_visible()

    chat_tab.click()
    expect(page.locator("#tab-chat")).to_be_visible()
    expect(page.get_by_placeholder("Ask about the selected code…")).to_be_visible()
    expect(page.get_by_role("button", name="Send")).to_be_visible()

    repositories_tab.click()
    checkboxes = page.locator("#repo-list input[type=checkbox]")
    count = checkboxes.count()
    assert count > 0, "The deployed repository catalog is empty"

    page.get_by_role("button", name="Select all").click()
    for index in range(count):
        expect(checkboxes.nth(index)).to_be_checked()
    expect(page.locator("#selection-note")).to_contain_text(f"{count} selected")

    page.get_by_role("button", name="Clear").click()
    for index in range(count):
        expect(checkboxes.nth(index)).not_to_be_checked()
    expect(page.locator("#selection-note")).to_contain_text("0 selected")


def test_analysis_selection_summary_tracks_repository_changes(page: Page) -> None:
    repository_cards = page.locator("#repo-list .cu-repo")
    count = repository_cards.count()
    assert count >= 2, "At least two repositories are needed to check the analysis summary"

    first_repo = repository_cards.nth(0)
    second_repo = repository_cards.nth(1)
    first_name = first_repo.locator(".cu-repo-title").inner_text()
    first_branch = first_repo.locator(".cu-chips .cu-chip").first.inner_text()
    second_name = second_repo.locator(".cu-repo-title").inner_text()
    second_branch = second_repo.locator(".cu-chips .cu-chip").first.inner_text()

    page.get_by_role("button", name="Clear").click()
    page.locator('.cu-tabs button[data-tab="pipelines"]').click()
    summary = page.locator("#pipeline-selection")
    expect(summary).to_contain_text("None selected")

    page.locator('.cu-tabs button[data-tab="repos"]').click()
    first_repo.locator("input").check()
    second_repo.locator("input").check()
    page.locator('.cu-tabs button[data-tab="pipelines"]').click()
    expected_summary = f"{first_name} @ {first_branch}, " f"{second_name} @ {second_branch}"
    expect(summary).to_contain_text(expected_summary)

    page.locator('.cu-tabs button[data-tab="repos"]').click()
    page.get_by_role("button", name="Clear").click()
    page.locator('.cu-tabs button[data-tab="pipelines"]').click()
    expect(summary).to_contain_text("None selected")


@pytest.mark.skip(reason="Indexing yappb is too large and slow for the UI E2E suite")
def test_analysis_of_yappb_returns_report(page: Page) -> None:
    repo_cards = page.locator("#repo-list .cu-repo")
    preloaded_repo = None
    for index in range(repo_cards.count()):
        candidate = repo_cards.nth(index)
        name = candidate.locator(".cu-repo-title").inner_text().strip()
        branch = candidate.locator(".cu-chips .cu-chip").first.inner_text().strip()
        if name == ANALYSIS_REPO_NAME and branch == ANALYSIS_REPO_BRANCH:
            preloaded_repo = candidate
            break
    assert (
        preloaded_repo is not None
    ), f"Repository {ANALYSIS_REPO_NAME} @ {ANALYSIS_REPO_BRANCH} is missing from the catalog"

    page.get_by_role("button", name="Clear").click()
    preloaded_repo.locator("input").check()
    page.locator('.cu-tabs button[data-tab="pipelines"]').click()
    expect(page.locator("#pipeline-selection")).to_contain_text(
        f"{ANALYSIS_REPO_NAME} @ {ANALYSIS_REPO_BRANCH}"
    )

    with page.expect_response(
        lambda response: response.url.endswith("/api/v2/pipelines")
        and response.request.method == "POST",
        timeout=30_000,
    ) as submit_info:
        page.get_by_role("button", name="Run Analysis").click()

    submit_response = submit_info.value
    submit_text = submit_response.text()
    assert (
        submit_response.ok
    ), f"Analysis submission failed: {submit_response.status} {submit_text}"
    run_id = submit_response.json().get("job_id")
    assert run_id, f"Analysis submission returned no run ID: {submit_text}"

    run_url = f"{page.url.rstrip('/')}/api/v2/pipelines/runs/{run_id}"
    deadline = time.monotonic() + PIPELINE_TIMEOUT_MS / 1000
    snapshot = {}
    while time.monotonic() < deadline:
        run_response = page.request.get(run_url, timeout=30_000)
        run_text = run_response.text()
        assert run_response.ok, f"Could not read analysis run: {run_response.status} {run_text}"
        snapshot = run_response.json()
        status = str(snapshot.get("status", "")).lower()
        if status in {"failed", "cancelled", "error"}:
            pytest.fail(f"Analysis run {run_id} ended with status {status}: {snapshot}")
        report = snapshot.get("analysis_report")
        if status == "succeeded" and isinstance(report, str) and report.strip():
            break
        time.sleep(15)

    assert snapshot.get("status") == "succeeded", (
        f"Analysis run {run_id} did not succeed within {PIPELINE_TIMEOUT_MS // 60_000} minutes: "
        f"{snapshot}"
    )
    analysis_report = snapshot.get("analysis_report")
    assert (
        isinstance(analysis_report, str) and analysis_report.strip()
    ), f"Analysis run {run_id} succeeded but returned no analysis report"

    page.reload(wait_until="domcontentloaded")
    created_run = page.locator(f'#runs .cu-run-expand[data-run-id="{run_id}"]')
    expect(created_run).to_be_visible(timeout=30_000)


def test_recent_run_can_open_its_analysis_report(page: Page) -> None:
    expand_buttons = page.locator("#runs .cu-run-expand")
    if expand_buttons.count() == 0:
        pytest.skip("No successful pipeline runs are available to open.")

    page.locator('.cu-tabs button[data-tab="pipelines"]').click()
    expand_buttons.first.click()

    report_viewer = page.locator("#report-viewer")
    expect(report_viewer).to_be_visible(timeout=30_000)
    expect(page.locator("#report-viewer-title")).not_to_be_empty()


def test_index_bundle_can_be_downloaded_and_uploaded_for_chat(
    page: Page,
    tmp_path: Path,
) -> None:
    """Exercise the index API and verify uploads appear in the current Chat UI."""
    console_url = page.url.rstrip("/")
    indexes_response = page.request.get(f"{console_url}/api/indexes", timeout=30_000)
    assert indexes_response.ok, f"Could not list indexes: {indexes_response.status}"
    indexes = indexes_response.json().get("indexes", [])
    source_index = next(
        (
            index
            for index in indexes
            if SOURCE_INDEX_NAME in str(index.get("git_slug", "")) and index.get("run_id")
        ),
        None,
    )
    assert source_index is not None, f"No indexed {SOURCE_INDEX_NAME} repository was found"

    download_response = page.request.get(
        f"{console_url}/api/indexes/{source_index['run_id']}/download",
        timeout=INDEX_TRANSFER_TIMEOUT_MS,
    )
    assert download_response.ok, f"Index download failed: {download_response.status}"
    filename = download_response.headers.get("content-disposition", "")
    assert ".tar.gz" in filename.lower(), f"Unexpected download headers: {filename!r}"

    downloaded_archive = tmp_path / "source-index.tar.gz"
    downloaded_archive.write_bytes(download_response.body())
    uploaded_slug = f"e2e-copy-{uuid4().hex[:10]}"
    uploaded_archive = tmp_path / f"{uploaded_slug}.tar.gz"
    copy_bundle_with_new_slug(downloaded_archive, uploaded_archive, uploaded_slug)

    upload_response = page.request.post(
        f"{console_url}/api/indexes/upload",
        multipart={
            "file": {
                "name": uploaded_archive.name,
                "mimeType": "application/gzip",
                "buffer": uploaded_archive.read_bytes(),
            }
        },
        timeout=INDEX_TRANSFER_TIMEOUT_MS,
    )
    response_text = upload_response.text()
    assert upload_response.ok, f"Index upload failed: {upload_response.status} {response_text}"
    assert upload_response.json().get("git_slug") == uploaded_slug

    page.reload(wait_until="domcontentloaded")
    page.locator('.cu-tabs button[data-tab="chat"]').click()
    repository = page.locator("#chat-repo")
    uploaded_label = f"{uploaded_slug} (uploaded)"
    option = repository.get_by_role("option", name=uploaded_label, exact=True)
    expect(option).to_have_count(1, timeout=30_000)
    repository.select_option(label=uploaded_label)
    expect(repository).to_have_value(f"uploaded|{uploaded_slug}")


def test_chat_query_returns_answer_and_succeeds(page: Page) -> None:
    page.locator('.cu-tabs button[data-tab="chat"]').click()

    repository = page.locator("#chat-repo")
    option = repository.get_by_role("option", name="tic-tac-toe-sample @ main", exact=True)
    expect(option).to_have_count(1, timeout=30_000)
    repository.select_option(label="tic-tac-toe-sample @ main")

    question = "How does the game determine when a player wins?"
    page.get_by_placeholder("Ask about the selected code…").fill(question)
    with page.expect_response(
        lambda response: response.url.endswith("/api/v2/queries")
        and response.request.method == "POST",
        timeout=30_000,
    ) as query_response_info:
        page.get_by_role("button", name="Send").click()

    query_response = query_response_info.value
    response_text = query_response.text()
    assert query_response.ok, f"Query submission failed: {query_response.status} {response_text}"
    query_id = query_response.json().get("query_id")
    assert query_id, f"Query submission did not return a query ID: {response_text}"

    answer = page.locator("#chat-log .cu-bubble.assistant:not(.thinking-msg)").last
    expect(answer).to_be_visible(timeout=QUERY_TIMEOUT_MS)
    expect(answer).not_to_be_empty(timeout=QUERY_TIMEOUT_MS)
    answer_text = answer.inner_text().strip()
    assert answer_text, "The query response box is empty"
    for error_text in (
        "Could not perform query:",
        "Query failed:",
        "The query job finished but no answer was found",
    ):
        assert error_text not in answer_text, f"Query returned an error: {answer_text}"

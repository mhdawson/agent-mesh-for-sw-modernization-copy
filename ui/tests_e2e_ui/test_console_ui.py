"""Browser smoke checks for the deployed Code Understanding console."""

import io
import json
from pathlib import Path
import tarfile

from playwright.sync_api import Page, expect

QUERY_TIMEOUT_MS = 30 * 60 * 1000
INDEX_TRANSFER_TIMEOUT_MS = 10 * 60 * 1000
SOURCE_INDEX_NAME = "tic-tac-toe-sample"
UPLOADED_INDEX_NAME = "copy-of-tic-tac-toe-sample"


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
                    manifest_bytes = json.dumps(
                        manifest, indent=2, sort_keys=True
                    ).encode("utf-8")
                    member.size = len(manifest_bytes)
                    copied.addfile(member, io.BytesIO(manifest_bytes))
                elif member.isfile():
                    member_file = original.extractfile(member)
                    assert member_file is not None, f"Downloaded index member is unreadable: {member.name}"
                    with member_file:
                        copied.addfile(member, member_file)
                else:
                    copied.addfile(member)


def test_console_loads_repositories_and_live_panels(page: Page) -> None:
    checkboxes = page.locator("#repo-list input[type=checkbox]")
    expect(checkboxes.first).to_be_visible()

    # These panels should render either their data or the normal empty state.
    expect(page.locator("#indexes")).not_to_be_empty()
    expect(page.locator("#jobs")).not_to_be_empty()


def test_tabs_and_repository_selection_work(page: Page) -> None:
    repositories_tab = page.locator('.cu-tabs button[data-tab="repos"]')
    analysis_tab = page.locator('.cu-tabs button[data-tab="pipelines"]')
    chat_tab = page.locator('.cu-tabs button[data-tab="chat"]')

    expect(page.locator("#tab-repos")).to_be_visible()
    analysis_tab.click()
    expect(page.locator("#tab-pipelines")).to_be_visible()
    expect(page.get_by_role("button", name="Run Code Understanding")).to_be_visible()

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
    expected_summary = (
        f"{first_name} @ {first_branch}, "
        f"{second_name} @ {second_branch}"
    )
    expect(summary).to_contain_text(expected_summary)

    page.locator('.cu-tabs button[data-tab="repos"]').click()
    page.get_by_role("button", name="Clear").click()
    page.locator('.cu-tabs button[data-tab="pipelines"]').click()
    expect(summary).to_contain_text("None selected")


def test_tic_tac_toe_index_can_be_copied_and_uploaded_with_new_name(
    page: Page,
    tmp_path: Path,
) -> None:
    source_title = page.locator("#indexes").get_by_text(SOURCE_INDEX_NAME, exact=True)
    expect(source_title).to_be_visible(timeout=30_000)
    source_card = source_title.locator("xpath=../..")

    with page.expect_download(timeout=INDEX_TRANSFER_TIMEOUT_MS) as download_info:
        source_card.get_by_role("button", name="Download index").click()

    download = download_info.value
    original_filename = download.suggested_filename
    assert SOURCE_INDEX_NAME in original_filename, (
        f"Expected a {SOURCE_INDEX_NAME} bundle, got {original_filename!r}"
    )
    assert original_filename.lower().endswith(".tar.gz"), (
        f"Expected a .tar.gz index download, got {original_filename!r}"
    )
    downloaded_archive = tmp_path / original_filename
    download.save_as(downloaded_archive)
    uploaded_archive = tmp_path / f"{UPLOADED_INDEX_NAME}.tar.gz"
    copy_bundle_with_new_slug(downloaded_archive, uploaded_archive, UPLOADED_INDEX_NAME)

    page.locator("#index-upload-file").set_input_files(uploaded_archive)
    expect(page.locator("#banner")).to_contain_text(
        "Index uploaded successfully.", timeout=INDEX_TRANSFER_TIMEOUT_MS
    )

    uploaded_title = page.locator("#indexes").get_by_text(
        UPLOADED_INDEX_NAME, exact=True
    )
    expect(uploaded_title).to_be_visible(timeout=INDEX_TRANSFER_TIMEOUT_MS)
    uploaded_card = uploaded_title.locator("xpath=../..")
    expect(uploaded_card.locator(".cu-chip.uploaded")).to_be_visible(
        timeout=INDEX_TRANSFER_TIMEOUT_MS
    )

    page.locator('.cu-tabs button[data-tab="chat"]').click()
    repository = page.locator("#chat-repo")
    uploaded_option_label = f"{UPLOADED_INDEX_NAME} (uploaded)"
    uploaded_option = repository.get_by_role(
        "option", name=uploaded_option_label, exact=True
    )
    expect(uploaded_option).to_have_count(1, timeout=30_000)
    repository.select_option(label=uploaded_option_label)
    expect(repository).to_have_value(f"uploaded|{UPLOADED_INDEX_NAME}")


def test_chat_query_returns_answer_and_succeeds(page: Page) -> None:
    page.locator('.cu-tabs button[data-tab="chat"]').click()

    repository = page.locator("#chat-repo")
    option = repository.get_by_role("option", name="tic-tac-toe-sample @ main", exact=True)
    expect(option).to_have_count(1, timeout=30_000)
    repository.select_option(label="tic-tac-toe-sample @ main")

    question = "How does the game determine when a player wins?"
    page.get_by_placeholder("Ask about the selected code…").fill(question)
    with page.expect_response(
        lambda response: response.url.endswith("/api/query")
        and response.request.method == "POST",
        timeout=30_000,
    ) as query_response_info:
        page.get_by_role("button", name="Send").click()

    query_response = query_response_info.value
    response_text = query_response.text()
    assert query_response.ok, f"Query submission failed: {query_response.status} {response_text}"
    job_name = query_response.json().get("job_name")
    assert job_name, f"Query submission did not return a job name: {response_text}"

    answer = page.locator("#chat-log .cu-bubble.assistant:not(.thinking-msg)").last
    expect(answer).to_be_visible(timeout=QUERY_TIMEOUT_MS)
    answer_text = answer.inner_text().strip()
    assert answer_text, "The query response box is empty"
    for error_text in (
        "Could not perform query:",
        "Query failed:",
        "The query job finished but no answer was found",
    ):
        assert error_text not in answer_text, f"Query returned an error: {answer_text}"

    job_card = page.locator("#jobs .cu-job-card").filter(has_text=job_name)
    expect(job_card).to_contain_text("Query", timeout=QUERY_TIMEOUT_MS)
    expect(job_card.locator(".cu-chip")).to_have_text("Succeeded", timeout=QUERY_TIMEOUT_MS)

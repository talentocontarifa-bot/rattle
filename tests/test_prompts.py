"""
Regression tests for Rattle's prompt construction.

The hourly/daily prompts are giant f-strings; a single unescaped `{name}` inside
an example snippet crashes every run with NameError before the LLM is even called
(this happened on 2026-10-01 and broke the hourly workflow for a week).

Run:  python -m pytest tests -q     (or)     python tests/test_prompts.py
"""
import os
import sys
import shutil
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


class _PromptCaptured(Exception):
    pass


def _load_rattle(tmpdir):
    os.environ.setdefault("GEMINI_API", "test-key")
    import rattle  # noqa: E402
    rattle.DB_FILE = os.path.join(tmpdir, "rattle_test.db")
    rattle.init_db()
    return rattle


def test_hourly_prompt_builds():
    tmpdir = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        rattle = _load_rattle(tmpdir)
        os.chdir(tmpdir)  # avoid touching real knowledge/index files
        captured = {}

        def fake_llm(prompt, *a, **kw):
            captured["prompt"] = prompt
            raise _PromptCaptured()

        rattle.call_gemini_with_retry = fake_llm
        rattle.log_iteration = lambda *a, **kw: None
        rattle.generate_static_dashboard = lambda: None

        rattle.hourly_task()  # must NOT raise NameError/KeyError while building the prompt

        prompt = captured.get("prompt")
        assert prompt, "hourly_task never reached the LLM call"
        assert "Post: {title}" in prompt, "example snippet braces were not preserved"
        assert "start_obscura_cdp" not in prompt, "prompt advertises a function that does not exist"
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_advertised_helpers_exist():
    """Every helper the prompt tells the LLM to call must actually exist in the exec globals."""
    tmpdir = tempfile.mkdtemp()
    try:
        rattle = _load_rattle(tmpdir)
        for name in ["post_to_kofi", "check_kofi_stats", "obscura_fetch", "get_stealth_browser",
                     "generate_speech", "render_video", "generate_nvidia_image",
                     "send_telegram_message", "send_telegram_voice", "send_telegram_video",
                     "send_telegram_photo"]:
            assert callable(getattr(rattle, name, None)), f"missing helper: {name}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_hourly_prompt_builds()
    test_advertised_helpers_exist()
    print("OK - prompts build correctly")

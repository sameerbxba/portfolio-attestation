"""The transcript the README quotes, pinned.

The README shows what `python -m attest.cli run` prints. Without this test that
claim decays the first time anyone edits the register, a digest changes, and
nothing goes red. It also puts attest/cli.py under test, which was otherwise
the only module in the package with no coverage at all.

If this fails after a deliberate change, regenerate with:

    python -m attest.cli run | sed '$ d' | sed '$ d' > tests/golden/run_2026-Q4.txt

and update the excerpt in README.md to match.
"""

from __future__ import annotations

from pathlib import Path

from attest import cli

GOLDEN = Path(__file__).resolve().parent / "golden" / "run_2026-Q4.txt"
README = Path(__file__).resolve().parent.parent / "README.md"


def run_cli(tmp_path, capsys) -> str:
    rc = cli.main(["run", "--log", str(tmp_path / "evidence.jsonl")])
    assert rc == 0
    out = capsys.readouterr().out
    # Drop the trailing evidence-log line, whose path depends on where the
    # log was written, and the blank line after it.
    return "\n".join(out.splitlines()[:-2]) + "\n"


def test_the_transcript_matches_the_recorded_one(tmp_path, capsys):
    assert run_cli(tmp_path, capsys) == GOLDEN.read_text()


def test_every_transcript_line_the_readme_quotes_is_really_printed(tmp_path, capsys):
    """The README's excerpt is an excerpt, but every line in it is verbatim.

    An interviewer who runs the command and sees different text stops trusting
    the rest of the page, and they are right to.
    """
    printed = set(run_cli(tmp_path, capsys).splitlines())
    readme = README.read_text().splitlines()

    # Two-space indentation matters here. The coverage block and the
    # escalation lines are printed at two spaces, and an earlier version of
    # this filter required four. It therefore skipped the two numbers the
    # whole project exists to contrast, and they could be rewritten to
    # anything at all with the suite still green.
    quoted = [
        line for line in readme
        if line.startswith("  ") and (
            "APP-0" in line
            or line.strip().startswith(("response rate", "COVERAGE", "day "))
        )
    ]
    assert len(quoted) >= 14, (
        f"the README quotes only {len(quoted)} transcript lines; "
        "the filter is probably no longer matching them"
    )
    for anchor in ("response rate", "COVERAGE"):
        assert any(line.strip().startswith(anchor) for line in quoted), (
            f"the README no longer quotes the {anchor} line"
        )

    missing = [line for line in quoted if line not in printed]
    assert not missing, "README quotes lines the CLI does not print:\n" + "\n".join(missing)


def test_the_readme_states_the_real_number_of_tests():
    """The one quoted figure that was not asserted.

    It could be changed to anything with the suite green, which is exactly the
    kind of small untrue claim an interviewer notices after running the tests.
    """
    words = {
        "Fifty": 50, "Sixty": 60, "Seventy": 70, "Eighty": 80, "Ninety": 90,
    }
    units = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9,
    }
    claimed = None
    for line in README.read_text().splitlines():
        if " tests, one or more per control" not in line:
            continue
        spelled = line.split(" tests")[0].strip().rstrip(",")
        tens, _, unit = spelled.partition("-")
        claimed = words[tens] + units.get(unit.lower(), 0)
    assert claimed is not None, "the README no longer states a test count"

    actual = 0
    for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
        actual += sum(
            1 for line in path.read_text().splitlines()
            if line.startswith("def test_")
        )
    assert claimed == actual, (
        f"README claims {claimed} tests; there are {actual}"
    )

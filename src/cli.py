import subprocess
import sys


def run_command(cmd: list[str]) -> int:
    """Executes a command and returns the exit code."""
    print(f"\n==> Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def run_tests_with_coverage() -> int:
    """Runs pytest with per-file test coverage reporting for the src directory."""
    return run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "--cov=src",
            "--cov-report=term-missing",
        ]
    )


def lock_dependencies(output_file: str = "requirements.txt") -> int:
    """Locks installed environment packages into requirements.txt."""
    print(f"\n🔒 Locking dependencies into {output_file}...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = result.stdout.splitlines()

        # Filter out editable local package installation lines (e.g. -e .)
        filtered_lines = [
            line
            for line in lines
            if not line.startswith("-e ")
            and "supply_chain_platform" not in line.lower()
            and "supply-chain-platform" not in line.lower()
        ]

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(filtered_lines) + "\n")

        print(
            f"✅ Successfully locked {len(filtered_lines)} dependencies into {output_file}"
        )
        return 0
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to lock dependencies: {e.stderr}")
        return 1


def main():
    if len(sys.argv) < 2:
        print("Usage: pykit [check | fix | build | deps lock]")
        sys.exit(1)

    # Join subsequent args to support multi-word subcommands like 'deps lock'
    args = [arg.lower() for arg in sys.argv[1:]]
    command = " ".join(args)

    if command == "check":
        rc_ruff = run_command(["ruff", "check", "."])
        rc_fmt = run_command(["ruff", "format", "--check", "."])
        rc_pytest = run_tests_with_coverage()
        sys.exit(rc_ruff or rc_fmt or rc_pytest)

    elif command == "fix":
        print("\n🔧 Applying automatic fixes and formatting...")
        run_command(["ruff", "check", ".", "--fix", "--unsafe-fixes"])
        run_command(["ruff", "format", "."])
        rc_check = run_command(["ruff", "check", "."])
        if rc_check != 0:
            print("\n⚠️ Some linting issues require manual fixes.")
        rc_pytest = run_tests_with_coverage()
        sys.exit(rc_check or rc_pytest)

    elif command == "build":
        rc_build = run_command([sys.executable, "-m", "build"])
        sys.exit(rc_build)

    elif command in ("deps lock", "deps:lock"):
        rc_lock = lock_dependencies("requirements.txt")
        sys.exit(rc_lock)

    else:
        print(
            f"Unknown command: '{' '.join(sys.argv[1:])}'. Available: check, fix, build, deps lock"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

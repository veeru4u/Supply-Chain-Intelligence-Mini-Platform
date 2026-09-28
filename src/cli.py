import importlib.metadata
import re
import subprocess
import sys
import tomllib  # Python 3.11+ standard library


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
    """Extracts only [project.dependencies] from pyproject.toml and pins their

    installed versions.
    """
    print(f"\n🔒 Generating {output_file} strictly from pyproject.toml...")
    try:
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)

        project_deps = data.get("project", {}).get("dependencies", [])
        if not project_deps:
            print("⚠️ No dependencies found under [project.dependencies].")
            return 1

        locked_lines = []
        for dep in project_deps:
            # Extract normalized package name (handles 'fastapi>=0.104.1', 'uvicorn[standard]', etc.)
            pkg_name = re.split(r"[><=~!;\[]", dep.strip())[0].strip().replace("_", "-")
            try:
                version = importlib.metadata.version(pkg_name)
                locked_lines.append(f"{pkg_name}=={version}")
            except importlib.metadata.PackageNotFoundError:
                # Fall back to the original constraint if package is not locally installed
                locked_lines.append(dep.strip())

        # Sort alphabetically for deterministic output
        locked_lines.sort()

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(locked_lines) + "\n")

        print(
            f"✅ Locked {len(locked_lines)} packages from pyproject.toml into {output_file}:\n"
        )
        for line in locked_lines:
            print(f"   • {line}")

        return 0

    except Exception as e:
        print(f"❌ Failed to generate {output_file}: {e}")
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

import subprocess
import sys
import os


def run_tests():
    project_dir = "/Users/slvtveter/Desktop/PycharmProjects/bot_tg"
    venv_python = os.path.join(project_dir, ".venv", "bin", "python")
    test_suite_path = os.path.join(project_dir, "tests", "test_suite.py")

    print("=" * 60)
    print("🚀 Starting Test Runner (20 Iterations loop to check for flakiness) ...")
    print("=" * 60)

    success_count = 0
    failure_count = 0
    failed_runs = []

    for i in range(1, 21):
        print(f"🔄 Iteration {i:02d}/20: ", end="", flush=True)

        # Execute python -m unittest tests/test_suite.py
        result = subprocess.run(
            [venv_python, "-m", "unittest", test_suite_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=project_dir,
        )

        if result.returncode == 0:
            success_count += 1
            print("🟢 SUCCESS")
        else:
            failure_count += 1
            failed_runs.append(i)
            print("🔴 FAILED")
            print("-" * 50)
            print(f"Error logs for iteration {i}:")
            print(result.stderr)
            print("-" * 50)

    print("\n" + "=" * 60)
    print("📊 Test Summary Statistics:")
    print("• Total Runs: 20")
    print(f"• Successes: {success_count}/20")
    print(f"• Failures:  {failure_count}/20")

    if failure_count == 0:
        print("🎉 100% SUCCESS! No flaky test failures detected.")
        sys.exit(0)
    else:
        print(f"⚠️ Flaky test failures detected in iterations: {failed_runs}")
        sys.exit(1)


if __name__ == "__main__":
    run_tests()

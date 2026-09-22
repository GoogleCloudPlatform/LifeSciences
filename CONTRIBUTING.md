# How to Contribute

We'd love to accept your patches and contributions to this project!

## Before You Begin

### 1. Sign our Contributor License Agreement (CLA)

Contributions to this project must be accompanied by a
[Contributor License Agreement](https://cla.developers.google.com/about) (CLA).
You (or your employer) retain the copyright to your contribution; this simply
gives us permission to use and redistribute your contributions as part of the
project.

If you or your current employer have already signed the Google CLA (even if it
was for a different project), you probably don't need to do it again.

Visit <https://cla.developers.google.com/> to see your current agreements or to
sign a new one.

### 2. Review our Community Guidelines

This project follows
[Google's Open Source Community Guidelines](https://opensource.google/conduct/).

---

## Contribution Process

### Opening Issues

If you encounter a bug or want to propose a feature for one of the applications
(`FoldRun`, `Pharma on Gemini Enterprise`, `Sentinel`, etc.), please open a
[GitHub Issue](https://github.com/GoogleCloudPlatform/LifeSciences/issues) and
specify the affected application in the issue template.

### Code Quality & Pre-Submission Checks

Before opening a Pull Request, please run the relevant checks locally inside the
application directory you modified:

1. **Apache 2.0 License Headers:**
   All source files must carry the standard Google LLC Apache 2.0 header:
   ```bash
   go install github.com/google/addlicense@latest
   addlicense -check -ignore "**/*.toml" -ignore "**/patches/**" -ignore "**/vendor/**" -ignore "**/third_party/**" .
   ```
2. **Python Formatting & Linting (Ruff):**
   ```bash
   ruff format --check --exclude '*.md' .
   ruff check --exclude '*.md' .
   ```
3. **Python Unit Tests (`uv` + `pytest`):**
   ```bash
   uv sync --frozen
   uv run --frozen pytest tests/unit
   ```
4. **Node / Frontend Linting & Formatting (if applicable):**
   ```bash
   npm ci
   npm run lint
   ```
5. **Terraform Formatting (if applicable):**
   ```bash
   terraform fmt -check -recursive
   ```

### Pull Request Review & Merge Lifecycle

1. **Automated GitHub Checks:** When you open a Pull Request against `main`,
   GitHub Actions (`.github/workflows/pr-checks.yml`) and the `cla/google` bot
   automatically validate your changes.
2. **Maintainer Review & Verification:** Once a repository maintainer reviews
   and approves your Pull Request on GitHub, the change is imported for final
   end-to-end verification before merge.
3. **Automatic Merge:** When verification completes and the change is merged,
   the commit is synced to `main` on GitHub and **automatically marks your Pull
   Request as Merged**.
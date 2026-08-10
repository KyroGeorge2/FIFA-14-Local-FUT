# Put this project on GitHub

The repository files are already prepared with `.gitignore`, `.gitattributes`, issue templates, a basic GitHub Actions check, and release-packaging scripts.

## Fastest method

1. Create a new **empty** repository on GitHub. Do not add a README, `.gitignore`, or license there because this folder already contains them.
2. Run `SETUP_GITHUB_REPO.cmd` once in this folder.
3. Run `PUSH_TO_GITHUB.cmd` and paste the repository URL, for example:
   `https://github.com/YOUR-NAME/FIFA-14-Local-FUT.git`

Git for Windows is required. The project's prerequisite installer can install Git if it is missing.

## Browser-only method

You can also create an empty GitHub repository and upload the contents of this folder using **Add file -> Upload files**. Make sure the hidden `.github`, `.gitignore`, and `.gitattributes` files are included.

## Make a downloadable release ZIP

Run:

`PACKAGE_RELEASE.cmd`

The clean runtime ZIP will be created under `dist\`. Upload that ZIP to a GitHub Release rather than committing generated ZIPs into the repository.

## Important

Do not commit generated certificates, runtime state, diagnostics, `.venv`, or `artifacts`; these are excluded by `.gitignore`.

Do not add FIFA 14 executables, EA DLLs, game archives, account credentials, or other files copied from a user's game installation to the repository.

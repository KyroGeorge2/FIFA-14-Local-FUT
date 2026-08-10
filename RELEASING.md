# Creating a GitHub Release

1. Update the version/release notes in the repository.
2. Commit and push the changes.
3. Run `PACKAGE_RELEASE.cmd` to create a clean runtime ZIP under `dist\`.
4. On GitHub, open **Releases -> Draft a new release**.
5. Create a tag such as `v2.41.1-beta2.25.9`.
6. Upload the ZIP from `dist\` as the release asset.
7. Copy the relevant changelog notes into the release description and publish the release.

The release packager excludes repository-only files and local/generated state. It does not bundle a FIFA 14 installation.

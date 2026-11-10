# vm-results

This branch holds results only, never code. GitHub Actions puts the Windows driver build for each
commit of `windows-l2cap-driver` here: `ci/<run>-<sha>.md` is the build report (compiler warnings,
`/analyze` findings, InfVerif output, failing lines first) and `ci/<sha>/driver.zip` the test-signed
driver (`.sys`, `.inf`, `.cat`, `.cer`), so the Windows test VM can fetch it with plain git and no
GitHub login. The VM test script pushes one folder per test round to `rounds/` (report, per-variant
results, driver log, setup details and crash analysis if there was one).

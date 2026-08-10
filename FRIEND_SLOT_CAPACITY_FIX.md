# Friend fresh-profile slot-capacity hotfix

This package fixes the launch failure:

`patch patched payload needs 21142 bytes, slot capacity is 20544`

The helperFunctions branch patcher now:

1. tries several compatible raw-DEFLATE settings and keeps the smallest valid ChunkZip payload;
2. if the patched payload still cannot fit in the original physical slot, appends it at a 16-byte-aligned location at the end of the same BIG archive and updates only the matching BH offset/size record;
3. preserves the exact retail record and BH backup, so the existing recovery path still restores the original mapping;
4. recognizes and verifies a known branch-only payload when it is installed at the relocated offset.

The fallback never writes through the next archive record.

# 2026-04-28 daily output email check

## Source

User-provided email body captured on 2026-05-06.

Context:

- Source screen in the email: `日別出力 -> 当日袋分け一覧`.
- Numbers without parentheses are manually checked actual counts.
- Numbers in parentheses are the system-displayed counts at the time of the email.
- 藍テラス could not be OCR-corrected, so its counts were added to the parenthesized system counts.
- いこいの森 糖尿 is served the same as 常食, so its 糖尿 counts were added to the parenthesized 常食 counts.
- The email states that 禁食 matched, while 常食 was too high, 軟菜/ミキサー were too low, and 常食+軟菜+ミキサー totals did not match.

## Email Body

4/28の食数を調べてみました。（日別出力→当日袋分け一覧の数字を拾いました）

（）なしの数字が実際の食数で、（）がシステムにより表示された人数です。

藍テラスのみOCR修正ができなかったので、藍テラスの食数は（）の数字に足しています。

それと、いこいの森の糖尿は常食と同じものを出しているので、それも常食の（）の数字に足しています。

禁食は実際の量とシステムから出た量が全て合っていました。

常食は実際より多く、軟菜ミキサーは少ないです。総数（常食＋軟菜＋ミキサー）も合いません。

4/28

朝食 野菜の卵とじ 常食300（297） 軟菜24（18） ミキサー20（9）

朝食 ブロッコリーの和え物 常食300（287）軟菜24（18）ミキサー20（9）

昼食 豆腐ハンバーグ 常食382（429） 軟菜28（22） ミキサー20（13）

昼食 ピーマンしりしり 常食373（420）禁食9（9） 軟菜28（22）ミキサー20（13）

昼食 オーロラサラダ 常食379（426）禁食3（3） 軟菜28（22）ミキサー20（13）

夕食 豚肉とじゃが芋の醤油炒め 常食371（416）禁食6（6）軟菜28（22）ミキサー20（13）

夕食 冬瓜の水晶煮 常食377（422）軟菜28（22）ミキサー20（13）

夕食 春雨サラダ 常食377（421）禁食1（1）軟菜28（22）ミキサー20（13）

納品書は、当日納品書exelを押すと、「一括ダウンロードに失敗しました」という表示が出ました。

施設ごとに見ていくと、納品書の形式で出てきた施設とラベルと似た感じのエクセルが出てくる施設がありました。（いこいの森、さくらなど）

納品書の形式で出てきた施設も、献立名が朝のメニューが夕にあったり、朝に昼の副菜が出てきたりとバラバラでした。

食数もきれいに反映されていませんでした。そよかぜは軟菜が2F、3Fと別れてますが、一緒になっており、数は2Fと3Fを足した数が表示されていました。

## Current stg Evidence

Fetched from stg API on 2026-05-06:

- `GET /api/orders/daily-bags?date=2026-04-28`
- `GET /api/totals?date=2026-04-28`

The daily-bags response had `order_count=14` and `group_count=9`. For the compared menu/diet rows, `daily-bags` and `totals` returned the same quantities.

Temporary evidence files from the fetch:

- `tmp/daily-output-20260428/daily-bags.json`
- `tmp/daily-output-20260428/daily-bags.tsv`
- `tmp/daily-output-20260428/totals.json`
- `tmp/daily-output-20260428/totals.tsv`

## Comparison Against Current stg

`actual` is the email's manually checked count. `email_system` is the parenthesized value in the email. `current_stg` is the current stg daily-bags/totals value. Deltas are `current_stg - actual` and `current_stg - email_system`.

| daypart | menu | diet | actual | email_system | current_stg | stg-actual | stg-email_system | note |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 朝 | 野菜の卵とじ | 常食 | 300 | 297 | 306 | +6 | +9 | current stg also has 糖尿=4 separately |
| 朝 | 野菜の卵とじ | 軟菜 | 24 | 18 | 21 | -3 | +3 |  |
| 朝 | 野菜の卵とじ | ミキサー | 20 | 9 | 16 | -4 | +7 |  |
| 朝 | ブロッコリーの和え物 | 常食 | 300 | 287 | 306 | +6 | +19 | current stg also has 糖尿=4 separately |
| 朝 | ブロッコリーの和え物 | 軟菜 | 24 | 18 | 21 | -3 | +3 |  |
| 朝 | ブロッコリーの和え物 | ミキサー | 20 | 9 | 16 | -4 | +7 |  |
| 昼 | 豆腐ハンバーグ | 常食 | 382 | 429 | 386 | +4 | -43 | current stg menu name is `豆腐ﾊﾝﾊﾞｰｸﾞ`; 糖尿=5 and placeholder=50 also present |
| 昼 | 豆腐ハンバーグ | 軟菜 | 28 | 22 | 25 | -3 | +3 |  |
| 昼 | 豆腐ハンバーグ | ミキサー | 20 | 13 | 20 | 0 | +7 |  |
| 昼 | ピーマンしりしり | 常食 | 373 | 420 | 377 | +4 | -43 | current stg also has 糖尿=5 and placeholder=50 |
| 昼 | ピーマンしりしり | 禁食 | 9 | 9 | 9 | 0 | 0 |  |
| 昼 | ピーマンしりしり | 軟菜 | 28 | 22 | 25 | -3 | +3 |  |
| 昼 | ピーマンしりしり | ミキサー | 20 | 13 | 20 | 0 | +7 |  |
| 昼 | オーロラサラダ | 常食 | 379 | 426 | 383 | +4 | -43 | current stg also has 糖尿=5 and placeholder=50 |
| 昼 | オーロラサラダ | 禁食 | 3 | 3 | 3 | 0 | 0 |  |
| 昼 | オーロラサラダ | 軟菜 | 28 | 22 | 25 | -3 | +3 |  |
| 昼 | オーロラサラダ | ミキサー | 20 | 13 | 20 | 0 | +7 |  |
| 夕 | 豚肉とじゃが芋の醤油炒め | 常食 | 371 | 416 | 385 | +14 | -31 | current stg also has 糖尿=5 and placeholder=48 |
| 夕 | 豚肉とじゃが芋の醤油炒め | 禁食 | 6 | 6 | 3 | -3 | -3 | email says 禁食 matched, but current stg does not match this row |
| 夕 | 豚肉とじゃが芋の醤油炒め | 軟菜 | 28 | 22 | 25 | -3 | +3 |  |
| 夕 | 豚肉とじゃが芋の醤油炒め | ミキサー | 20 | 13 | 20 | 0 | +7 |  |
| 夕 | 冬瓜の水晶煮 | 常食 | 377 | 422 | 391 | +14 | -31 | current stg also has 糖尿=5 and placeholder=48 |
| 夕 | 冬瓜の水晶煮 | 軟菜 | 28 | 22 | 24 | -4 | +2 |  |
| 夕 | 冬瓜の水晶煮 | ミキサー | 20 | 13 | 20 | 0 | +7 |  |
| 夕 | 春雨サラダ | 常食 | 377 | 421 | 450 | +73 | +29 | current stg also has 糖尿=5 and placeholder=48 |
| 夕 | 春雨サラダ | 禁食 | 1 | 1 | 1 | 0 | 0 | current stg key is `sesame_allergy`, not `forbidden` |
| 夕 | 春雨サラダ | 軟菜 | 28 | 22 | 25 | -3 | +3 |  |
| 夕 | 春雨サラダ | ミキサー | 20 | 13 | 20 | 0 | +7 |  |

## Updated Comparison: Ignore Parentheses

User clarified that the parenthesized numbers should be ignored for the current investigation because the email was written about two weeks before the current stg state. This section compares only:

- `email_actual`: number without parentheses in the email.
- `current_stg`: current stg `日別出力 -> 当日袋分け一覧` API value.
- `diff`: `current_stg - email_actual`.

### Diet-Level Comparison

| daypart | menu | diet | email_actual | current_stg | diff | note |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 朝 | 野菜の卵とじ | 常食 | 300 | 306 | +6 | current stg raw lines are regular=304 + staff=2 |
| 朝 | 野菜の卵とじ | 軟菜 | 24 | 21 | -3 | saved line total is already 21 |
| 朝 | 野菜の卵とじ | ミキサー | 20 | 16 | -4 | saved line total is already 16 |
| 朝 | ブロッコリーの和え物 | 常食 | 300 | 306 | +6 | current stg raw lines are regular=304 + staff=2 |
| 朝 | ブロッコリーの和え物 | 軟菜 | 24 | 21 | -3 | saved line total is already 21 |
| 朝 | ブロッコリーの和え物 | ミキサー | 20 | 16 | -4 | saved line total is already 16 |
| 昼 | 豆腐ハンバーグ | 常食 | 382 | 386 | +4 | current stg raw lines are regular=346 + daycare=38 + staff=2 |
| 昼 | 豆腐ハンバーグ | 軟菜 | 28 | 25 | -3 | saved line total is already 25 |
| 昼 | 豆腐ハンバーグ | ミキサー | 20 | 20 | 0 |  |
| 昼 | ピーマンしりしり | 常食 | 373 | 377 | +4 | current stg raw lines are regular=340 + daycare=35 + staff=2 |
| 昼 | ピーマンしりしり | 禁食 | 9 | 9 | 0 | no_fish=9 is bucketed to forbidden |
| 昼 | ピーマンしりしり | 軟菜 | 28 | 25 | -3 | saved line total is already 25 |
| 昼 | ピーマンしりしり | ミキサー | 20 | 20 | 0 |  |
| 昼 | オーロラサラダ | 常食 | 379 | 383 | +4 | current stg raw lines are regular=344 + daycare=37 + staff=2 |
| 昼 | オーロラサラダ | 禁食 | 3 | 3 | 0 | no_meat=3 is bucketed to forbidden |
| 昼 | オーロラサラダ | 軟菜 | 28 | 25 | -3 | saved line total is already 25 |
| 昼 | オーロラサラダ | ミキサー | 20 | 20 | 0 |  |
| 夕 | 豚肉とじゃが芋の醤油炒め | 常食 | 371 | 385 | +14 | current stg raw lines are regular=369 + staff=16 |
| 夕 | 豚肉とじゃが芋の醤油炒め | 禁食 | 6 | 3 | -3 | current stg has no_meat=3 only |
| 夕 | 豚肉とじゃが芋の醤油炒め | 軟菜 | 28 | 25 | -3 | saved line total is already 25 |
| 夕 | 豚肉とじゃが芋の醤油炒め | ミキサー | 20 | 20 | 0 |  |
| 夕 | 冬瓜の水晶煮 | 常食 | 377 | 391 | +14 | current stg raw lines are regular=375 + staff=16 |
| 夕 | 冬瓜の水晶煮 | 軟菜 | 28 | 24 | -4 | saved line total is already 24 |
| 夕 | 冬瓜の水晶煮 | ミキサー | 20 | 20 | 0 |  |
| 夕 | 春雨サラダ | 常食 | 377 | 450 | +73 | 大和なでしこ `ORD2a654d51` has saved line `92`; if this is corrected to `32`, current_stg becomes `390` and diff becomes `+13` |
| 夕 | 春雨サラダ | 禁食 | 1 | 1 | 0 | current stg key is sesame_allergy=1 |
| 夕 | 春雨サラダ | 軟菜 | 28 | 25 | -3 | saved line total is already 25 |
| 夕 | 春雨サラダ | ミキサー | 20 | 20 | 0 |  |

### Regular + Soft + Mixer Total, Parentheses Ignored

| daypart | menu | email_actual total | current_stg total | diff | note |
| --- | --- | ---: | ---: | ---: | --- |
| 朝 | 野菜の卵とじ | 344 | 343 | -1 |  |
| 朝 | ブロッコリーの和え物 | 344 | 343 | -1 |  |
| 昼 | 豆腐ハンバーグ | 430 | 431 | +1 |  |
| 昼 | ピーマンしりしり | 421 | 422 | +1 |  |
| 昼 | オーロラサラダ | 427 | 428 | +1 |  |
| 夕 | 豚肉とじゃが芋の醤油炒め | 419 | 430 | +11 | regular includes staff=16 |
| 夕 | 冬瓜の水晶煮 | 425 | 435 | +10 | regular includes staff=16 |
| 夕 | 春雨サラダ | 425 | 495 | +70 | if 大和なでしこ 92->32, total becomes 435 and diff becomes +10 |

## Cause Investigation, Parentheses Ignored

Current findings:

- `日別出力 -> 当日袋分け一覧` is not creating the large values by arithmetic duplication. The daily output matches the persisted confirmed order lines after diet bucketing.
- The largest mismatch is `春雨サラダ / 常食`. Current stg has `450`, but `ORD2a654d51` 大和なでしこ contributes `92` for this single row. Other 大和なでしこ rows around it are `31` or `32`, so this is consistent with the user's observation that `92` is likely a `32` mistake. Correcting only that saved line removes `60` from the mismatch.
- `夕食 / 常食` residual differences are mostly explained by current bucketing: `staff` is aggregated into `regular`. For dinner rows, current raw lines have `staff=16`, while raw `regular` alone is slightly below the email actual by `2-3`.
- Lunch `常食` rows include `daycare` and `staff` in the current `regular` bucket. This appears broadly aligned with the email actual, because the remaining diff is only `+4` for each lunch row.
- `軟菜` is consistently low by `3-4` on current stg. Since daily output equals saved order-line totals for these rows, the missing amount is upstream in saved order values, not in daily-output summation.
- Morning `ミキサー` is low by `4`; lunch/dinner `ミキサー` matches. This also points to saved order values for the morning rows rather than a global mixer aggregation bug.
- Many current 4/28 confirmed orders now return `template_version_mismatch` or `selected_ocr_required` from `workflow-v2/sheet-source`. That means some current daily-output rows are persisted confirmed lines from older OCR/template states and are not safely re-derivable through the current workflow-v2 sheet-source path without reselecting/rebuilding OCR.

## Current stg Summary

- The current stg values no longer match the email's parenthesized system values. This means the currently deployed stg output is not the same output that was observed when the email was written.
- Compared with the manually checked actual values, current stg is close for most lunch rows, but still off for morning 軟菜/ミキサー and especially 夕食 常食.
- `春雨サラダ` is the largest current mismatch: 常食 is `450` vs actual `377`, a +73 difference.
- `豚肉とじゃが芋の醤油炒め` now has 禁食 `3` on stg, while the email actual/system pair is `6 (6)`.
- Current stg output includes `placeholder` rows for lunch and dinner in both `/daily-bags` and `/totals`. Those rows were not included in the comparison table above because the email compares 常食/軟菜/ミキサー/禁食, but their presence in daily output is a separate item to check.
- Current stg keeps いこいの森 糖尿 as `diabetes` rows in the API output. The email's parenthesized 常食 values already included that diabetes count manually, so any final reconciliation must define whether 糖尿 should be merged into 常食 at daily-output time or only in a specific downstream view.

## Regular + Soft + Mixer Total Check

The email specifically says the total of `常食 + 軟菜 + ミキサー` does not match. 禁食 is excluded from this total because the email defines the problematic total as 常食+軟菜+ミキサー.

| daypart | menu | actual total | email_system total | current_stg total | stg-actual | stg-email_system |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 朝 | 野菜の卵とじ | 344 | 324 | 343 | -1 | +19 |
| 朝 | ブロッコリーの和え物 | 344 | 314 | 343 | -1 | +29 |
| 昼 | 豆腐ハンバーグ | 430 | 464 | 431 | +1 | -33 |
| 昼 | ピーマンしりしり | 421 | 455 | 422 | +1 | -33 |
| 昼 | オーロラサラダ | 427 | 461 | 428 | +1 | -33 |
| 夕 | 豚肉とじゃが芋の醤油炒め | 419 | 451 | 430 | +11 | -21 |
| 夕 | 冬瓜の水晶煮 | 425 | 457 | 435 | +10 | -22 |
| 夕 | 春雨サラダ | 425 | 456 | 495 | +70 | +39 |

## Delivery Note Issue From Email

The email reports:

- `当日納品書Excel` shows `一括ダウンロードに失敗しました`.
- Some facility-level downloads look like delivery notes, while others look like labels.
- Even delivery-note-shaped outputs have menu/daypart mismatches.
- そよかぜ 軟菜 2F/3F is merged in the output instead of remaining split.

API probe on 2026-05-06:

- `GET /api/outputs/daily-bundle?date=2026-04-28&bundle_type=delivery` did not return within roughly 100 seconds and was stopped locally.
- This was not counted as a successful reproduction of the exact browser error, but it is still a failure mode for the operator flow because the request is too slow or stuck for interactive use.

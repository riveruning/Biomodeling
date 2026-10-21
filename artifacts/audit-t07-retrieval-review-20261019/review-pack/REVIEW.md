# T07 检索材料审核包

全部为已见合成开发材料；领域审核尚未完成。
字符偏移基于 packet.json 中的原页文本，不是 UTF-8 字节偏移。相关、完整覆盖与科学支持分开审核。
review-forms.json 仅预填内容哈希；审核人、带时区时间、备注和检查项均需实际审核后填写。
审核记录是归属声明，不认证身份或专业资质。正文/补充/更正应保持同一家庭；本包保守归为一个同源开发家庭。
来源副本供阅读；packet.json 为评分文本权威。修改材料或标签后必须重新生成哈希并重新审核。

## parameter_late

类别：parameter；预期：answerable；状态：pending
内容 SHA256：`d85810d7bb650ee22612df572f5822464c424699f4248c192d355ed8820e0172`
查询（原文）：
```json
"k_deg parameter"
```
- main / paper / 页 1：[原页文本](sources/parameter_late--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2298,
    "end": 2363,
    "quote": "The degradation parameter k_deg = 0.12 per minute in condition A.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## initial_late

类别：initial；预期：answerable；状态：pending
内容 SHA256：`5494b4143546b05a7c387c3c6bd4dd648bfe4b6334380674c80382140a3ab823`
查询（原文）：
```json
"X initial"
```
- main / paper / 页 1：[原页文本](sources/initial_late--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2301,
    "end": 2356,
    "quote": "The initial amount X(0) = 2.5 nmol before the stimulus.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## unit_late

类别：unit；预期：answerable；状态：pending
内容 SHA256：`87a0bf1961d19511310967724c77152eea803e24bcb909a48db47b166f961fb5`
查询（原文）：
```json
"k_on unit"
```
- main / paper / 页 1：[原页文本](sources/unit_late--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2293,
    "end": 2347,
    "quote": "The parameter k_on has unit litre per mole per second.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## formula_late

类别：formula；预期：answerable；状态：pending
内容 SHA256：`7b6f3e4e3ff4bbdfcab6192dbb5e5351c40b7ee99c066ab96f3a91061e9d0552`
查询（原文）：
```json
"X equation"
```
- main / paper / 页 1：[原页文本](sources/formula_late--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2290,
    "end": 2346,
    "quote": "The state X follows the equation dX/dt = k_in - k_out*X.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## qualifier

类别：parameter；预期：answerable；状态：pending
内容 SHA256：`df3f0d8774b3c99e157dada39d9bf3ac000c470fb8116bdd5db4d0565a86960f`
查询（原文）：
```json
"k_cat parameter"
```
- main / paper / 页 1：[原页文本](sources/qualifier--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2289,
    "end": 2399,
    "quote": "For untreated cells only, the parameter k_cat = 0.8 per second; this value must not be used for treated cells.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## supplement

类别：supplement；预期：answerable；状态：pending
内容 SHA256：`440a0ebd1f88674d11e4130c1608e6e1dcb5b10dada7497e32195016ae411c74`
查询（原文）：
```json
"k_bind parameter"
```
- main / main / 页 1：[原页文本](sources/supplement--main--p1.txt)
- supplement / supplement / 页 2：[原页文本](sources/supplement--supplement--p2.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "supplement",
    "page": 2,
    "start": 0,
    "end": 71,
    "quote": "Table S2: the parameter k_bind = 0.07 per second for the binding assay.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## conflict_same_page

类别：conflict；预期：conflicting；状态：pending
内容 SHA256：`39fe32c57524b55bbb83bbbe45fc26b0594d29cf5c331b1b41da53d35325d016`
查询（原文）：
```json
"k_decay parameter"
```
- main / paper / 页 3：[原页文本](sources/conflict_same_page--paper--p3.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 3,
    "start": 0,
    "end": 54,
    "quote": "The parameter k_decay = 0.1 per second in the Methods.",
    "id": "a1",
    "role": "conflict_candidate"
  },
  {
    "source_id": "paper",
    "page": 3,
    "start": 2298,
    "end": 2378,
    "quote": "The parameter k_decay = 0.3 per second in Figure 2; this conflicts with Methods.",
    "id": "a2",
    "role": "conflict_candidate"
  }
]
```

## conflict_two_sources

类别：conflict；预期：conflicting；状态：pending
内容 SHA256：`ffabf36a1a4a234333e1bf0a4194cf4b3b4b208f25bae4c5946f98e95ca2f8e6`
查询（原文）：
```json
"k_decay parameter"
```
- main / main / 页 1：[原页文本](sources/conflict_two_sources--main--p1.txt)
- supplement / supplement / 页 1：[原页文本](sources/conflict_two_sources--supplement--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "main",
    "page": 1,
    "start": 0,
    "end": 54,
    "quote": "The parameter k_decay = 0.1 per second in the Methods.",
    "id": "a1",
    "role": "conflict_candidate"
  },
  {
    "source_id": "supplement",
    "page": 1,
    "start": 0,
    "end": 80,
    "quote": "The parameter k_decay = 0.3 per second in Figure 2; this conflicts with Methods.",
    "id": "a2",
    "role": "conflict_candidate"
  }
]
```

## symbol_substring

类别：parameter；预期：answerable；状态：pending
内容 SHA256：`9ce366532d6e190dbde0d09400e99664bfda8e931f25a1adc23aebbeb88a5e04`
查询（原文）：
```json
"k"
```
- main / paper / 页 1：[原页文本](sources/symbol_substring--paper--p1.txt)
- main / paper / 页 2：[原页文本](sources/symbol_substring--paper--p2.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 2,
    "start": 0,
    "end": 35,
    "quote": "The loss constant k = 0.2 per hour.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## underscore_symbol

类别：parameter；预期：answerable；状态：pending
内容 SHA256：`3a07c4bbd6193bba77efd912061b586c42cb68b2eeefeaa23ea16465db730b66`
查询（原文）：
```json
"Na_K"
```
- main / paper / 页 1：[原页文本](sources/underscore_symbol--paper--p1.txt)
- main / paper / 页 4：[原页文本](sources/underscore_symbol--paper--p4.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 4,
    "start": 0,
    "end": 37,
    "quote": "The Na_K pump rate is 3.0 per second.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## unicode_offsets

类别：unit；预期：answerable；状态：pending
内容 SHA256：`ecbc973f674620a309d5ddb6a21aa438fd40aef2e9c86a2a2c8410b24c7d3b85`
查询（原文）：
```json
"β_decay unit"
```
- main / paper / 页 1：[原页文本](sources/unicode_offsets--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2319,
    "end": 2392,
    "quote": "The β_decay parameter has unit minute^-1; preserve the original β symbol.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## no_match

类别：no_match；预期：not_located；状态：pending
内容 SHA256：`b5db23c1589b48084c793ed02899241554c10b29071d1970f5599ef9aac7f94b`
查询（原文）：
```json
"missing_token"
```
- main / paper / 页 1：[原页文本](sources/no_match--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[]
```

## untrusted_instructions

类别：formula；预期：answerable；状态：pending
内容 SHA256：`a68bb198125aa82cccc263b1f9223e3916eac1b2ac3dc4a7640501320f76d398`
查询（原文）：
```json
"X equation"
```
- main / paper / 页 1：[原页文本](sources/untrusted_instructions--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2331,
    "end": 2397,
    "quote": "The model equation is dX/dt = -0.5*X; no other terms are reported.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## multiple_initials

类别：initial；预期：answerable；状态：pending
内容 SHA256：`0334669b35c5e4c573e99ddf7f95481e6dff955c609321afcb136661f3c5a579`
查询（原文）：
```json
"initial state"
```
- main / paper / 页 1：[原页文本](sources/multiple_initials--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 0,
    "end": 32,
    "quote": "The initial state X(0) = 2 nmol.",
    "id": "a1",
    "role": "support_candidate"
  },
  {
    "source_id": "paper",
    "page": 1,
    "start": 2276,
    "end": 2308,
    "quote": "The initial state Y(0) = 4 nmol.",
    "id": "a2",
    "role": "support_candidate"
  }
]
```

## missing_is_evidence

类别：missing；预期：explicit_missing；状态：pending
内容 SHA256：`f4bc187bd6329f7e88ab9c4f1df56c482436ebd6e0ac7f0c746a1264b19314a9`
查询（原文）：
```json
"k_missing parameter"
```
- main / paper / 页 1：[原页文本](sources/missing_is_evidence--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 2306,
    "end": 2367,
    "quote": "The parameter k_missing was not reported; it remains unknown.",
    "id": "a1",
    "role": "missing_statement"
  }
]
```

## lexical_gap

类别：wording_gap；预期：answerable；状态：pending
内容 SHA256：`dd3c44e680c1bcb66c580b3e86ee812c3e2dfddca0fc5b46d2eb05cb1c2da9a9`
查询（原文）：
```json
"starting population"
```
- main / paper / 页 1：[原页文本](sources/lexical_gap--paper--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 0,
    "end": 55,
    "quote": "At t=0, N=12 cells; N is the state used in dN/dt = r*N.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## long_paragraph

类别：initial；预期：answerable；状态：pending
内容 SHA256：`0ff8468ae2d372a23d233035b42fc0bf22d29c8b28117fc768e7ce4de1160231`
查询（原文）：
```json
"initial X concentration"
```
- main / paper / 页 7：[原页文本](sources/long_paragraph--paper--p7.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 7,
    "start": 630,
    "end": 707,
    "quote": "The initial X concentration is 1.2 micromolar, with time measured in minutes.",
    "id": "a1",
    "role": "support_candidate"
  }
]
```

## clarification_scope

类别：conflict；预期：conflicting；状态：pending
内容 SHA256：`b48d7518288df0167805a711c701609527240c03b6747f5183c32f1872b0b26f`
查询（原文）：
```json
"initial X amount"
```
- main / paper / 页 1：[原页文本](sources/clarification_scope--paper--p1.txt)
- clarification / clarification / 页 1：[原页文本](sources/clarification_scope--clarification--p1.txt)

标注备注与审核优先项：
```json
"Hand-specified synthetic target spans; pending independent review. Neither a paper gold standard nor unseen evaluation. Review priority: condition A/B statements may be compatible; the legacy conflict label is only a retrieval candidate, not a verified scientific contradiction."
```
候选标注（请核对目标、条件/单位、缺失与矛盾；候选支持不是事实证明）：
```json
[
  {
    "source_id": "paper",
    "page": 1,
    "start": 0,
    "end": 48,
    "quote": "The initial X amount is 2 nmol in the main text.",
    "id": "a1",
    "role": "conflict_candidate"
  },
  {
    "source_id": "clarification",
    "page": 1,
    "start": 0,
    "end": 116,
    "quote": "Researcher clarification: the initial X amount is 5 nmol for condition B; the earlier 2 nmol applies to condition A.",
    "id": "a2",
    "role": "conflict_candidate"
  }
]
```

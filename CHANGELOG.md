# Changelog

## [6.4.0](https://github.com/Beancount-Trans/Beancount-Trans-Backend/compare/6.3.0...6.4.0) (2026-11-14)

### Features

* **file-manager:** 新增账本 ZIP 导出接口 ([8021c27](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/8021c278c704eb013e3457b899d47c273f315326))
* **translate:** 写入接口返回首次写入标记 ([0e669d6](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/0e669d675c22d8ae230afbb020d4a21a2b331a5c))

## [6.3.0](https://github.com/Beancount-Trans/Beancount-Trans-Backend/compare/6.2.0...6.3.0) (2026-11-13)

### Features

* **account:** 官方模板描述全覆盖并自动生成解析账户描述 ([e1dcdb8](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/e1dcdb8ccd8fa99f9b053124d27b3f8a0c2d3a1a))
* **account:** 按托管态限制账户改名、币种与删除 ([95c56ab](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/95c56abe4372c407eee97d31590559a9b30ae646))
* **account:** 账户写入边界落地，支持改名与反向对账 ([bf7e658](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/bf7e65814b0dcfe84c307b4ef3f74134568c3778))
* **account:** 账户支持多币种并同步账本 open ([aa0a803](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/aa0a8038f20da88276fa0e2d02914b59effaa004))
* **account:** 账户新增/启停/删除同步账本 open/note/close ([e841254](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/e841254f3d2fb89b43c9e7e1ea496bb2525d1f92))
* **external-accounts:** 补充国内银行机构别名 ([80d8eff](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/80d8eff5d2e2f8fe2c65e90037fd40848e39740b))
* **git:** 同步后可选自动导入账本新增账户 ([11f502f](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/11f502f84314e5d882c6aeda853ea2761f0e5d8c))
* **git:** 同步默认自动导入账本新增账户 ([d22de96](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/d22de9637e7ed9fe4be2462cd2f56c50124be790))
* **reconciliation:** 对账时间点默认沿用当天或上一条已完成对账 ([c47e236](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/c47e23653d19094440987e2531045e7f5e015b67))
* **review:** 兜底账户条目标记需关注并统计 ([caff373](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/caff373c7ca28218e3e2ffe65cc4849aa334ddc2))
* **translate:** 解析成功后自动补齐资产账户与映射 ([64d9059](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/64d9059ed01e9d2253a05d1e30c777be2a530b50))

### Bug Fixes

* **account:** 删除账户前释放外部账户绑定 ([4558502](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/45585029f2456939651e817c1377cd7420371015))
* **account:** 实现平台账本 open 指令去重功能 ([f0df761](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/f0df7616149cdf9d0e2a73f35f569a7af4039116))
* **account:** 根账户不再落库并清理非法账本指令 ([41f3368](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/41f3368384f68fb8e5eb6c01abe4d8fa7b66c1a6))
* **account:** 账本已关闭账户允许清理平台记录 ([e3f20ed](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/e3f20ed3d5e2af6b11d7a42812adb7d9ea60d377))
* **git同步:** Git 用户从账本中同步更新平台账户的币种 ([fb7b3a7](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/fb7b3a772f23c30309f46ef03e12a951df050a9d))
* **reconciliation:** 自动补 pad 修复被后补交易打破的断言 ([1627712](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/1627712786b274348e02aa3ea83aba61d24d4cd2))
* **translate:** 分期还款条目不继承折扣，避免每期多补收入行 ([7b4b93b](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/7b4b93bf31d20ad2d49efe1857d73b379527d7e7))
* **translate:** 支付宝未匹配付款方式改落 Assets:Other ([fe20f16](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/fe20f163ec20a551ea730ac0a8b1003ca44cad64))
* 对账币种优先取账户配置，账户未配置时回退账本 ([9fe5a0d](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/9fe5a0df296e647df085f725fe2b5183270432c6))

## [6.2.0](https://github.com/Beancount-Trans/Beancount-Trans-Backend/compare/6.1.0...6.2.0) (2026-11-02)

### Features

* **parse:** 默认使用本地 BERT 并将平台 LLM 改为解析首页可选项 ([b7241c3](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/b7241c377826ca38ae45b3d29d2b01e17d65baa9))
* **trans:** 新增账单解析可用性统计 ([414c1e2](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/414c1e286f8f61f762e748de64b331dfa9a901f7))

### Bug Fixes

* **parse:** 解析失败时返回可分类的错误码与筛选计数 ([56914e3](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/56914e3d1e11f1d99124d7df58a277ec0af6c4a3))

## [6.1.0](https://github.com/Beancount-Trans/Beancount-Trans-Backend/compare/6.0.3...6.1.0) (2026-10-31)

### Features

* **usage:** 新增 Core 侧用量计量骨架 ([b8d1d9d](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/b8d1d9dcdc4e93fa346a3aa7507c99b36582f33e))

## [6.0.3](https://github.com/Beancount-Trans/Beancount-Trans-Backend/compare/6.0.2...6.0.3) (2026-10-31)

### Bug Fixes

* **recurrence:** 修复月度重复规则的月末日期计算问题 ([5dca10a](https://github.com/Beancount-Trans/Beancount-Trans-Backend/commit/5dca10a67a6ebf63730cd804feca9b60061d97b5))

## [6.0.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/6.0.1...6.0.2) (2026-10-24)

### Bug Fixes

* **entry-review:** 增加金额不平衡提示并阻断确认写入 ([17b6836](https://github.com/dhr2333/Beancount-Trans-Backend/commit/17b6836b158fa38edba3040658135e20b2d07f67))
* 修复循环待办生成的条目不可编辑的缺陷 ([4502242](https://github.com/dhr2333/Beancount-Trans-Backend/commit/4502242a4d051abd0ce1816008ee0ba1a0cf92a0))

## [6.0.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/6.0.0...6.0.1) (2026-10-19)

### Bug Fixes

* **assistant:** 为长助手任务添加 SSE 流式传输支持 ([b7a9ff1](https://github.com/dhr2333/Beancount-Trans-Backend/commit/b7a9ff1b87939f7fd4f37d2ea99c256a79dc3de3))
* **assistant:** 保留删除访问令牌后的共享账本绑定与所有者信息 ([6a46bef](https://github.com/dhr2333/Beancount-Trans-Backend/commit/6a46bef27608ca9a5fa1df53cb932be97fd6020b))
* **访问令牌:** 统一按实际读取数据更新最后使用时间 ([10c218a](https://github.com/dhr2333/Beancount-Trans-Backend/commit/10c218a209f44d2fec5b4c2cd89960d00154b813))

## [6.0.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.16.0...6.0.0) (2026-10-14)

### ⚠ BREAKING CHANGES

* **account:** Account/AccountTemplateItem 的 reconciliation_cycle_unit、
reconciliation_cycle_interval 字段被移除，前端与移动端需改用 reconciliation_rule。

### Features

* **account:** 账户对账周期迁移为 rrule 规则 ([ced5921](https://github.com/dhr2333/Beancount-Trans-Backend/commit/ced5921b9330ed2ed8e456ec204e2587a4bb95b4))
* **reconciliation:** 完善循环任务对齐补齐逻辑与文档 ([e4d4f35](https://github.com/dhr2333/Beancount-Trans-Backend/commit/e4d4f35d658f93bf2cef880035028ab2311a6486))
* **reconciliation:** 循环待办支持生成审核待办（固定模板） ([d70d21d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d70d21dda0f0eb2fe9d57944d6443637c69a4a9d))
* **reconciliation:** 循环待办新增到期产出类型 kind ([87be44d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/87be44d0eca0d061d17dc45256ec7fa3fc53c3fe))
* **reconciliation:** 循环待办日期即时对齐与卡片顺延 ([d0cf8a7](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d0cf8a7acae2637e328d2274360a6af2ed1d0516))
* **reconciliation:** 新增基于 rrule 的循环待办 ([b746932](https://github.com/dhr2333/Beancount-Trans-Backend/commit/b746932acd2f558f08f9f771ad3090d48813f917))

### Bug Fixes

* **reconciliation:** 修复循环任务完成基准计算逻辑并新增管理页展示字段 ([27fcdd7](https://github.com/dhr2333/Beancount-Trans-Backend/commit/27fcdd77d83df1baa010a58a3b80a8d6f2e979f8))
* **reconciliation:** 允许用户修改循环待办实例的执行日期 ([d9d2bd1](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d9d2bd13271cfa4e1e39293b23040a4bfdea00d3))
* **reconciliation:** 循环待办可选字段允许 null 并归一化为空串 ([822fdf8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/822fdf8c2850db83585cf5ba57faf8023b52e347))
* **reconciliation:** 循环待办基准日期即本期待办日期并前置预览创建后果 ([7412094](https://github.com/dhr2333/Beancount-Trans-Backend/commit/7412094ea14bb12c9fc7bc11b32b44595f5963b7))
* **reconciliation:** 生成审核待办不再支持「完成后起算」 ([e90cbcc](https://github.com/dhr2333/Beancount-Trans-Backend/commit/e90cbccfe8a3f7e04abeed262e4b8f4bcf333c37))
* **recurring_task:** 重构日期推算逻辑，修复历史锚点导致的日期锁定问题 ([8d13e47](https://github.com/dhr2333/Beancount-Trans-Backend/commit/8d13e47de5dc308a71fef437c4493d6e9f247ae4))

## [5.16.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.15.0...5.16.0) (2026-10-09)

### Features

* **reconciliation, translate:** 新增无用pad自动清理功能与调用点（先对账再解析的流程顺畅） ([5379b58](https://github.com/dhr2333/Beancount-Trans-Backend/commit/5379b58a2e2a73f8961ce60e565d32e1a33ae59e))

### Bug Fixes

* **split_entries:** 修复条目拆分时保留注释行的问题，新增测试用例 ([db00651](https://github.com/dhr2333/Beancount-Trans-Backend/commit/db00651f6ea04534253886e468d98ad450b880bf))

## [5.15.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.14.3...5.15.0) (2026-10-06)

### Features

* **git仓库:** 添加trans条目按月度账本迁移功能 ([23560d7](https://github.com/dhr2333/Beancount-Trans-Backend/commit/23560d78e968dd56139b758494f70e1556a6d56a))

### Bug Fixes

* **条目审核:** 添加重解析时的候选关键字批量传播功能 ([8f3e52d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/8f3e52d0fdb8fa9d1bed47b50e6dbb69b1544e2c))

## [5.14.3](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.14.2...5.14.3) (2026-09-30)

### Performance Improvements

* **assistant:** 修复简明模式和共享账本下的免责提示逻辑，重构重复代码 ([3925109](https://github.com/dhr2333/Beancount-Trans-Backend/commit/39251097fffccf9d003f231c16368e5a99a7f291))

## [5.14.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.14.1...5.14.2) (2026-09-30)

### Bug Fixes

* 新增聊天消息应答模式字段并优化查询记录过滤逻辑 ([c7f91ff](https://github.com/dhr2333/Beancount-Trans-Backend/commit/c7f91ff5f783e01aa5359e93c4e6e35b9041948c))

## [5.14.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.14.0...5.14.1) (2026-09-29)

### Bug Fixes

* **authentication/views:** 修改个人访问令牌的撤销逻辑为彻底删除 ([e3a3362](https://github.com/dhr2333/Beancount-Trans-Backend/commit/e3a33629cbbe509ab8aa5b2a55d5cc533b88c2bf))

### Performance Improvements

* **assistant:** 添加共享账本会话简明表达模式与账本记录字段 ([01dc1a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/01dc1a81d8fc314572bb9fb6519ef7bc059876b4))
* **assistant:** 补充余额查询需限制日期<=基准日期的规则 ([76e559d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/76e559d4ed467ec08055f5416325d8130633192a))

## [5.14.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.13.0...5.14.0) (2026-09-27)

### Features

* **assistant:** 优化共享账本处理逻辑、提示文案与参数校验 ([21411eb](https://github.com/dhr2333/Beancount-Trans-Backend/commit/21411eb0f11d3e980a43133cd3b4c239d2206978))
* **assistant:** 实现共享账本绑定与跨账本查询支持 ([c4fe172](https://github.com/dhr2333/Beancount-Trans-Backend/commit/c4fe17276239a352a16b2a4f97cb1f66d53a4d0f))
* **shared-ledger:** 新增共享账本可用性检查并优化提示词与错误处理 ([cea1a8d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/cea1a8de5c7fb51aa48977811acb1e813bc79736))
* **共享账本绑定:** 支持共享账本绑定使用多个别名 ([7015a50](https://github.com/dhr2333/Beancount-Trans-Backend/commit/7015a50b4eb27d85e8597c4cb51649dd6ad057a5))

## [5.13.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.12.1...5.13.0) (2026-09-26)

### Features

* **copilot-bookkeeping:** 实现Copilot自然语言记账全链路功能 ([36d63c0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/36d63c0977fe39652594cf304ea963e9c7a13a83))
* **file-manager:** 新增 collect 系统账本功能与存量用户迁移工具 ([7e77921](https://github.com/dhr2333/Beancount-Trans-Backend/commit/7e779219af2902ba0af6695d9c5457067cd38cae))
* **translate:** 新增 write-collect 解析条目写入接口 ([b98f55e](https://github.com/dhr2333/Beancount-Trans-Backend/commit/b98f55e3644c1ba0e36bd2b069dd8770f6d874e1))
* **translate:** 新增上传账单直接解析并加入审核队列的接口 ([6c0b4f3](https://github.com/dhr2333/Beancount-Trans-Backend/commit/6c0b4f3ad99449f2fb06653e4fb9de149c067179))

### Bug Fixes

* **authentication:** 配置JWT滑动续期并添加刷新测试用例 ([df788f3](https://github.com/dhr2333/Beancount-Trans-Backend/commit/df788f318ba43b3736f9b6846c8428deb0102f35))
* **Copilot:** 调整支付账户校验逻辑，仅校验账户是否存在 ([74fe96f](https://github.com/dhr2333/Beancount-Trans-Backend/commit/74fe96fbdd2ba4534068600c87f307dac3a5e348))
* **file utils:** 修正 Beancount 收集文件的条目分隔格式 ([2e89060](https://github.com/dhr2333/Beancount-Trans-Backend/commit/2e890604ef44515d3404e4f0a683d8d3fafb135c))
* **translate/copilot:** 为Copilot记账条目状态添加前缀 ([7256b24](https://github.com/dhr2333/Beancount-Trans-Backend/commit/7256b241b9df5a6099866fab9ce782937b69a936))
* **upload_parse_review:** 调整上传解析接口为固定审核模式，不受配置影响 ([07cf622](https://github.com/dhr2333/Beancount-Trans-Backend/commit/07cf62247498ca10fe6334240c5f018d0cd5641d))

## [5.12.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.12.0...5.12.1) (2026-09-16)

### Bug Fixes

* **mcp:** DCR 注册令牌过期时间 ([5cd6d57](https://github.com/dhr2333/Beancount-Trans-Backend/commit/5cd6d57f909c5e8378f38073e1cd0e1bdcc4a93f))

## [5.12.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.2...5.12.0) (2026-09-16)

### Features

* **mcp, auth:** 新增MCP账本服务及配套认证体系 ([5750993](https://github.com/dhr2333/Beancount-Trans-Backend/commit/575099301397adbcc97b32bdbb4bdc278f14f04f))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-15)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* **git_repository:** 完善各托管平台支持 ([dc8fb3d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/dc8fb3d8541dce73e0b0662cfae088b7d897d6fe))
* **git:** 新增取消同步 ([ad3b8bb](https://github.com/dhr2333/Beancount-Trans-Backend/commit/ad3b8bb8ad0e92fe6e43d2c9fbc5b02449f336c0))
* 用户账本在 `trans/` 中的目录结构与平台文件管理的目录结构保持一致 ([04bf390](https://github.com/dhr2333/Beancount-Trans-Backend/commit/04bf390d121f4cb26b2a16b75cd488efecf9b424))
* 账单完成解析后，不再以账单进行分组，而是统一为条目审核待办，重复条目自动去重 ([9117b12](https://github.com/dhr2333/Beancount-Trans-Backend/commit/9117b1283bcdb1311a436664487ed4ac2e303408))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-15)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* **git_repository:** 完善各托管平台支持 ([dc8fb3d](https://github.com/dhr2333/Beancount-Trans-Backend/commit/dc8fb3d8541dce73e0b0662cfae088b7d897d6fe))
* **git:** 新增取消同步 ([ad3b8bb](https://github.com/dhr2333/Beancount-Trans-Backend/commit/ad3b8bb8ad0e92fe6e43d2c9fbc5b02449f336c0))
* 用户账本在 `trans/` 中的目录结构与平台文件管理的目录结构保持一致 ([04bf390](https://github.com/dhr2333/Beancount-Trans-Backend/commit/04bf390d121f4cb26b2a16b75cd488efecf9b424))
* 账单完成解析后，不再以账单进行分组，而是统一为条目审核待办，重复条目自动去重 ([9117b12](https://github.com/dhr2333/Beancount-Trans-Backend/commit/9117b1283bcdb1311a436664487ed4ac2e303408))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-08)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-08)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-08)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-08)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.1...5.11.2) (2026-09-08)

### Bug Fixes

* "关闭默认忽略" 保留所有条目 ([1d8f6a8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/1d8f6a86bf8ea156f9d34a1e06495311f97f51f8))
* 首页解析对齐现有解析审核 ([bdbdab0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bdbdab04000d55aeb99ed8fdc196d8c3af95b952))

## [5.11.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.11.0...5.11.1) (2026-09-03)

### Bug Fixes

* 处理自动化测试失败 ([4d3bcb1](https://github.com/dhr2333/Beancount-Trans-Backend/commit/4d3bcb172361d80cb5483c2cc812a4e30966233a))

## [5.11.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.10.0...5.11.0) (2026-09-02)

### Features

* Copilot Fava 引用来源 ([d123342](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d1233426e8d5eab74fa0d5ca8ce5eec1324d330b))

### Bug Fixes

* 「Copilot 多轮按需查询」 ([a979b63](https://github.com/dhr2333/Beancount-Trans-Backend/commit/a979b632da2fff9b4d79afc99b1f5bc329d10f79))
* increase Celery task time limit for run_assistant_chat to improve processing time ([7371372](https://github.com/dhr2333/Beancount-Trans-Backend/commit/7371372065acae093fb30acd5ebcc7b13b210c3a))
* 修复以下问题 ([bc7d6e8](https://github.com/dhr2333/Beancount-Trans-Backend/commit/bc7d6e8ef75d6427f994a24c33ef3ef7b1c19b7b))
* 修复审核草稿一致性问题 ([09db352](https://github.com/dhr2333/Beancount-Trans-Backend/commit/09db35294fac4e0663cef431f93a77a98e2cb2cf))
* 修复或新增 Copilot 以下问题： ([6f14155](https://github.com/dhr2333/Beancount-Trans-Backend/commit/6f14155a4df434be21e4e71cf413143e9d022ae1))
* 解决设置页两套 DeepSeek Key 的冗余问题 ([aecad75](https://github.com/dhr2333/Beancount-Trans-Backend/commit/aecad75309b45e16bfbf36d6c52dd175ef156918))
* 解析审核支持新增资产映射（参考新增支出映射和新增收入映射） ([7dfbd65](https://github.com/dhr2333/Beancount-Trans-Backend/commit/7dfbd65a624be00d4f89f2c19bac6c3d81b30d1d))
* 请求只负责落库和投递任务，生成在 Celery 上跑；页面只订阅进度 ([307dd73](https://github.com/dhr2333/Beancount-Trans-Backend/commit/307dd73846db6308a8644e076d767991a56d8791))
* 预览保存为真源，删除条目后无空行 ([b6f2011](https://github.com/dhr2333/Beancount-Trans-Backend/commit/b6f2011c8b628ae603562cec27fbe6c819e0f118))

## [5.10.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.9.0...5.10.0) (2026-06-20)

### Features

* 更新 BQL 错误处理和能力说明 ([e6ea2dc](https://github.com/dhr2333/Beancount-Trans-Backend/commit/e6ea2dc0ca1dbfb25172826e13eafaa9007e5df7))
* 添加工具调用轮次上限配置 ([fb48b10](https://github.com/dhr2333/Beancount-Trans-Backend/commit/fb48b108e123e10eba3c5f353ac9f0563b04ef13))
* 添加深度思考模式支持 ([ef83ef3](https://github.com/dhr2333/Beancount-Trans-Backend/commit/ef83ef3ba7924b445a8c4045515758a68802196e))

## [5.9.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.12...5.9.0) (2026-06-20)

### Features

* 增强助手服务以支持洞察模式和相关 BQL 示例 ([3de41b0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/3de41b0fffe73d7997164a9db17417fa7f8f4831))

## [5.8.12](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.11...5.8.12) (2026-06-18)

### Bug Fixes

* 增强助手服务的 DSML 工具调用处理 ([3f45229](https://github.com/dhr2333/Beancount-Trans-Backend/commit/3f45229525082bfbb5b83b07cf2cd05cb379c55b))

## [5.8.11](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.10...5.8.11) (2026-06-18)

### Bug Fixes

* 提供基于标签的 BQL few-shot ([#91](https://github.com/dhr2333/Beancount-Trans-Backend/issues/91)) ([812b15f](https://github.com/dhr2333/Beancount-Trans-Backend/commit/812b15fd822089b52f7bbd8a7ff1e27e90ae78b2))

## [5.8.10](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.9...5.8.10) (2026-06-04)

### Bug Fixes

* defeat repair ([#87](https://github.com/dhr2333/Beancount-Trans-Backend/issues/87)) ([d7da6b3](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d7da6b38af2d4ad696d3adbef1e07916a4061af5))

## [5.8.9](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.8...5.8.9) (2026-05-13)

### Bug Fixes

* 修复以下缺陷 ([#86](https://github.com/dhr2333/Beancount-Trans-Backend/issues/86)) ([4bcc8ba](https://github.com/dhr2333/Beancount-Trans-Backend/commit/4bcc8baf7d548a36d4c27fb351980dcf4726ec24))

## [5.8.8](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.7...5.8.8) (2026-04-09)

### Bug Fixes

* admin 将待办类型与用户进行关联 ([#83](https://github.com/dhr2333/Beancount-Trans-Backend/issues/83)) ([d280f84](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d280f847ba3435a3fef03af05ae5152b0d8df21a))

## [5.8.7](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.6...5.8.7) (2026-04-09)

### Bug Fixes

* 解决以下问题 ([#82](https://github.com/dhr2333/Beancount-Trans-Backend/issues/82)) ([4822ce7](https://github.com/dhr2333/Beancount-Trans-Backend/commit/4822ce7a97021c396cabcba364d18fd921c7c471))

## [5.8.6](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.5...5.8.6) (2026-04-01)

### Bug Fixes

* 修复微信账单更新导致的解析失败，同时增加兼容性说明文档 ([#81](https://github.com/dhr2333/Beancount-Trans-Backend/issues/81)) ([35be626](https://github.com/dhr2333/Beancount-Trans-Backend/commit/35be6268794b133f90d5cc03612333297d032ba6))

## [5.8.5](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.4...5.8.5) (2026-03-11)

### Bug Fixes

* translate ([#78](https://github.com/dhr2333/Beancount-Trans-Backend/issues/78)) ([69deb54](https://github.com/dhr2333/Beancount-Trans-Backend/commit/69deb543d0d139a45c0d0c262e67b089556527e4))

## [5.8.4](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.3...5.8.4) (2026-03-05)

### Bug Fixes

* Add description field to Account and AccountTemplateItem models ([#74](https://github.com/dhr2333/Beancount-Trans-Backend/issues/74)) ([504bb52](https://github.com/dhr2333/Beancount-Trans-Backend/commit/504bb520380928f544796746cfcf9545fb822d27))

## [5.8.3](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.2...5.8.3) (2026-03-05)

### Bug Fixes

* Refactor official templates to use JSON data source ([#73](https://github.com/dhr2333/Beancount-Trans-Backend/issues/73)) ([510faa1](https://github.com/dhr2333/Beancount-Trans-Backend/commit/510faa16781093c77f9e38d0fafb96512af494c4))

## [5.8.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.1...5.8.2) (2026-02-06)

### Bug Fixes

* balance ([#72](https://github.com/dhr2333/Beancount-Trans-Backend/issues/72)) ([4c5873e](https://github.com/dhr2333/Beancount-Trans-Backend/commit/4c5873eaf8a6a361f0c52ad693d2a3c636e5c5db))

## [5.8.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.8.0...5.8.1) (2026-02-06)

### Bug Fixes

* balance ([#71](https://github.com/dhr2333/Beancount-Trans-Backend/issues/71)) ([3d29e99](https://github.com/dhr2333/Beancount-Trans-Backend/commit/3d29e995e664de064f727b2108cef49a4ce1f0a4))

## [5.8.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.7.0...5.8.0) (2026-02-06)

### Features

* Add date field validation for transaction items in reconciliation ([#69](https://github.com/dhr2333/Beancount-Trans-Backend/issues/69)) ([b8669cf](https://github.com/dhr2333/Beancount-Trans-Backend/commit/b8669cf3c4940805e33a810a32b3378012075b93))

## [5.7.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.6.1...5.7.0) (2026-02-04)

### Features

* 文件解析支持审核功能 ([#67](https://github.com/dhr2333/Beancount-Trans-Backend/issues/67)) ([aba134a](https://github.com/dhr2333/Beancount-Trans-Backend/commit/aba134a2fd1a41fbcd017ef253e0b5e68b3299cb))

## [5.6.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.6.0...5.6.1) (2026-02-01)

### Bug Fixes

* 对账无差额时忽略 `transaction_items` 条目 ([#66](https://github.com/dhr2333/Beancount-Trans-Backend/issues/66)) ([78d3e84](https://github.com/dhr2333/Beancount-Trans-Backend/commit/78d3e847952026a48757d9a2e553639a0f050854))

## [5.6.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.5.1...5.6.0) (2026-01-30)

### Features

* Automatically generate reconciliation tasks when creating a new user. ([#64](https://github.com/dhr2333/Beancount-Trans-Backend/issues/64)) ([c6b9bc0](https://github.com/dhr2333/Beancount-Trans-Backend/commit/c6b9bc0b6a144c0b856e66757e8a2262a3d9b13b))

## [5.5.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.5.0...5.5.1) (2026-01-29)

### Bug Fixes

* Ensure proper handling of parent-child relationships during account deletion ([#63](https://github.com/dhr2333/Beancount-Trans-Backend/issues/63)) ([20420f3](https://github.com/dhr2333/Beancount-Trans-Backend/commit/20420f36638fc6fed2e95eb6fdc2b7f99eb32300))

## [5.5.0](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.4.4...5.5.0) (2026-01-28)

### Features

* Add user filtering and optimization in ScheduledTask admin ([#62](https://github.com/dhr2333/Beancount-Trans-Backend/issues/62)) ([e03a632](https://github.com/dhr2333/Beancount-Trans-Backend/commit/e03a632547f3e23efffc13ddaf8cb71cf44fe195))

## [5.4.4](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.4.3...5.4.4) (2026-01-28)

### Bug Fixes

* 实现账户对账周期与待办管理的闭环 ([#58](https://github.com/dhr2333/Beancount-Trans-Backend/issues/58)) ([a91848a](https://github.com/dhr2333/Beancount-Trans-Backend/commit/a91848a5a53195c3124aaa20879fdb2f9d7f39e2))

## [5.4.3](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.4.2...5.4.3) (2026-01-26)

### Bug Fixes

* Prevent self-referencing parent accounts in Account model ([#56](https://github.com/dhr2333/Beancount-Trans-Backend/issues/56)) ([f0768fe](https://github.com/dhr2333/Beancount-Trans-Backend/commit/f0768feb0e5d8c44678053c4d3ba01295dfeb978))

## [5.4.2](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.4.1...5.4.2) (2026-01-14)

### Bug Fixes

* Update Account model to use PROTECT for parent relationship and enhance validation ([#54](https://github.com/dhr2333/Beancount-Trans-Backend/issues/54)) ([6deccb7](https://github.com/dhr2333/Beancount-Trans-Backend/commit/6deccb782c0363cf69f89a87bf14c273d917f539))

## [5.4.1](https://github.com/dhr2333/Beancount-Trans-Backend/compare/5.4.0...5.4.1) (2026-01-13)

### Bug Fixes

* Fixed the issue where anonymous users could not access the formatted output page. ([#52](https://github.com/dhr2333/Beancount-Trans-Backend/issues/52)) ([0382dd4](https://github.com/dhr2333/Beancount-Trans-Backend/commit/0382dd4e4910b82d208017cc35df95911af0ca87))

## 1.0.0 (2025-11-12)

### Features

* enhance asset and income tag management in AccountHandler and ExpenseHandler ([f1bd262](https://github.com/dhr2333/Beancount-Trans-Backend/commit/f1bd262d0f60c6be3e974b2ee759ebf75a595b81))
* support anonymous user access to data using default user ID=1 ([#29](https://github.com/dhr2333/Beancount-Trans-Backend/issues/29)) ([24cc6cf](https://github.com/dhr2333/Beancount-Trans-Backend/commit/24cc6cfbb6919b5cf8133d0b213879718a7cef2c))
* Template management adds billing file example ([#37](https://github.com/dhr2333/Beancount-Trans-Backend/issues/37)) ([d1ba404](https://github.com/dhr2333/Beancount-Trans-Backend/commit/d1ba404722b6434fe675238d34059226c29bcef4))

### Bug Fixes

* add null checks for asset retrieval in various views and handlers to prevent errors ([4ba959f](https://github.com/dhr2333/Beancount-Trans-Backend/commit/4ba959f829cbe13f3d5ffc26fad0daeabb02ddfe))
* Fixed an issue where the transfer-out and transfer-in symbols would always be swapped when automatically transferring funds from Alipay's Yu'ebao account. ([#42](https://github.com/dhr2333/Beancount-Trans-Backend/issues/42)) ([eb49fa3](https://github.com/dhr2333/Beancount-Trans-Backend/commit/eb49fa323eaca6f515c82fbf4392210a075006ec))

本文件将由 semantic-release 自动维护，用于记录 Beancount-Trans-Backend 的版本变化。

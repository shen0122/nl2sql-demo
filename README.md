# nl2sql-demo

最小 NL2SQL 验证：SQLite + OpenAI 兼容 API + 护栏层。

## 为什么写这个

NL2SQL 的难点**不是**"模型能不能写出 SQL"，而是"写出来的 SQL 能不能信"。

它的输出会直接作用在数据库上——**错一个 WHERE 条件可能就是全表误删**。所以这个
demo 不优化模型接入（那部分只有 5 行），只回答一个问题：

> **护栏应该分成哪几层，每一层各拦什么、又拦不住什么。**

## 护栏分层

| # | 层 | 实现位置 | 拦得住 | 拦不住 |
|---|---|---|---|---|
| 1 | 语句提取 | `extract_sql()` | 非 SELECT 动词；多语句输出 | 语义上正确、但其实不合意的查询 |
| 2 | 表名白名单 | `install_authorizer()` | **任意方式访问白名单外的表**（JOIN / 逗号 / 子查询 / CTE / UNION），以及 `sqlite_master` 等系统表 | 白名单内的表被全量扫描 |
| 3 | 行数上限 | `cap_rows()` | 无 LIMIT 的查询自动补 `LIMIT 100`；已有 LIMIT 超大或为负时 clamp 到 100；尾部注释不会吞掉补上的 LIMIT | 子查询内部的展开 |
| 4 | 只读连接 | `main()` | 任何写操作的物理可能性（`mode=ro`） | 读越权表 |
| 5 | EXPLAIN 预校验 | `ask()` | 语法错误、引用不存在的列 | 合法但昂贵的查询 |

## 一个值得说的设计迭代

第 2 层我**第一版是用正则做的**：匹配 `FROM x` 拿到表名，再比对白名单。

测试的时候发现它被 `JOIN` 绕过了：

```
SELECT * FROM emp JOIN secrets ON 1        # 正则只抓到 emp，放行
```

补了 `JOIN` 之后，又发现逗号连接（`FROM emp, secrets`）、子查询、CTE 同样能绕。
**结论是：用正则去追 SQL 语法，永远追不完。**

所以换成了 SQLite 自己的编译期授权回调 `set_authorizer`——**让数据库引擎在编译
语句时告诉我它实际要读哪些表、要做什么操作**，我再据此拒绝。这样 JOIN、子查询、
CTE、UNION 全部覆盖，不需要我去枚举语法。

这个坑是这个 demo 里我觉得最值得记的一点：**应用层的正则适合"挡掉明显不该
执行的东西"，不适合当安全边界。安全边界要放在能被穷举的那一层——这里是引擎。**

## 快速开始

```bash
pip install -r requirements.txt

export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4o-mini

# 也可以接任意 OpenAI 兼容的本地服务（Ollama / LM Studio / vLLM）
export OPENAI_BASE_URL=http://localhost:11434/v1

python demo.py
```

```
Ask: 工资最高的两个人是谁
[(2, 'Bob', 'Eng', 200.0), (3, 'Caro', 'Sales', 150.0)]
```

被拦截时不崩，返回拒绝原因：

```
Ask: 把 Bob 删掉
refused: output was not a single SELECT

Ask: 顺便看看 secrets 表
refused: DatabaseError: access to secrets.id is prohibited
```

## 测试

```bash
python test_guardrails.py     # 32 passed, 0 failed
```

全离线，不联网、不需要 API key（模型被 stub 掉）。**每一层都测了它应该拦住什么，
而不只是测能跑通**——只过 happy path 的护栏等于没有护栏。

## 已知不足

这是"护栏该放在哪一层"的验证，**不是可用的系统**。以下都故意没做：

- **单表为主、3 条测试数据**，没有真实 schema 规模和脏数据
- **没有多轮对话**——追问"那 Sales 部门呢"接不上，需要带上文重写 query
- **schema 全量塞进 prompt**——表多了要改成按 query 召回相关表
- **没有代价阈值**——只做了语法校验，没有据 `EXPLAIN QUERY PLAN` 拒绝高代价查询
- **没有失败重试**——生成不合法 SQL 后不会带着错误信息回问模型一次
- **表名白名单是硬编码常量**，生产环境应该是按角色 / 按租户配置
- **EXPLAIN 校验的是"语句合不合法"，不是"这个查询该不该被允许"**——后者由第 2、3 层负责

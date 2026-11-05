# nomods-neoforge1.21.1

一个用来**批量生成"只发一句 hello 的" NeoForge 1.21.1 模组**的小工具，以及它生成出来的一批模组。

每个模组（`nomods1`、`nomods2`、`nomods3`……）：

- modid 是 `nomodsN`，包名是 `com.chapple.nomodsN`；
- 注册**一个可选的双向网络通道**（payload 类型 `nomodsN:hello`，协议版本 `1`）；
- 玩家进服时，服务端通过这个通道发一条 `hello from nomodsN`；客户端收到后打印日志；
- 客户端没装这个模组时不会发送（先检查 `connection.hasChannel(...)`），不会把客户端踢掉；
- 作者：`mingzi_94387355`、`deepseek`；版本：`1.0.0-neoforge1.21.1`。

另外还提供**完全不含网络代码的空模组**（在 `dist-empty\`，当前是 `nomods1..1000000`）：
只有一个 `@Mod` 空类，不注册通道、不发 hello、不监听任何事件。空模组默认也从 1 编号，
因此和 hello 版 `nomods1..10000` 的 modid 重叠；如果两套要同时安装，构建空模组时加
`--start 10001`。构建方法见 [百万级空模组](#百万级空模组快速路径)。

## 环境要求

- **JDK 21**（Minecraft 1.21.1 / NeoForge 需要 Java 21，Gradle 本身也用它运行）；
- 首次构建需要联网，Gradle 会从 NeoForged 的 maven 下载 NeoForge 21.1.233 及其依赖；
- Python 3.10+（只有生成器需要，构建不需要）。

> 仓库只包含**工具脚本和示例模块 `nomods1..nomods10` 的源码**，
> 不包含 `dist\`、`dist-empty\` 里的生成 jar（后者 100 万个、约 1.3 GB），
> 需要时用下面的 `massbuild.py` / `fast_empty.py` 重新生成。

## 快速开始

```powershell
# 1. 生成（或补齐）模组，默认 nomods1 .. nomods10
python generate.py

# 2. 构建全部模组，jar 会收集到 .\dist
.\build.ps1
```

`build.ps1` 会自动寻找本机的 JDK 21 并设置 `JAVA_HOME`，然后执行 Gradle。
如果不想用脚本：

```powershell
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-21.0.13.0.2'
.\gradlew.bat build
```

产物在各自的 `nomodsN\build\libs\nomodsN-1.0.0-neoforge1.21.1.jar`，
`build.ps1` 还会把它们复制到 `dist\`。把 `dist\` 里的 jar 丢进 `mods` 文件夹即可。

> 要的是**上万个**模组、而不是几十个开发用的工程？直接看下面的
> [批量构建](#批量构建上千--上万个模组)：`python massbuild.py`。

## 批量构建（上千 / 上万个模组）

Gradle 方案适合几十个模组；上万个模组如果每个都建一个 Gradle 子项目，会生成
上万个 `build` 目录、几十 GB 的中间产物，配置阶段就会卡死。所以另有一个
**批量构建器** [massbuild.py](massbuild.py)：

```powershell
python massbuild.py                          # 构建 nomods1..10000（hello 版）-> dist\，约 2 分钟
python massbuild.py --count 50000            # 想更多就改数量
python massbuild.py --count 100 --out D:\m   # 小批量 / 换输出目录
python massbuild.py --keep-builds            # 保留中间产物（调试用）

# 空模组（不注册网络通道、不发 hello），编号从 10001 继续，输出到 dist-empty\
python massbuild.py --count 10000 --start 10001 --variant empty --out dist-empty
```

`--variant hello`（默认）生成带通道和 hello 的版本，`--variant empty` 生成只有
一个 `@Mod` 空类、完全不含网络代码的版本。

它只向 Gradle 要一次 NeoForge 编译 classpath，然后：

1. 在临时目录里渲染所有模组的源码（不会在项目里留下 1 万个目录）；
2. 用**一次 javac** 把几万个类全部编译完；
3. 用 Python 的 `zipfile` 逐个打包成 jar，打完一个就删掉这个模组的 class 文件；
4. 结束时删除临时工作区、所有 `nomods*/build` 和根目录 `build` —— **只留下输出目录
   里的 jar**。

实测：

- 10000 个 hello 模组：渲染 20 秒 + 编译 79 秒 + 打包 29 秒，共约 2 分 08 秒，
  产出 10000 个 jar、39.1 MB；
- 10000 个空模组（`nomods10001..20000`）：渲染 11 秒 + 编译 21 秒 + 打包 19 秒，
  共约 66 秒，产出 10000 个 jar、11.7 MB。

批量编译出的 class 与 Gradle 构建的 jar 逐字节一致（已对比 `nomods1` 的 SHA-256）。

> 第一次运行、或上一轮清理把 `nomods*/build` 删掉之后，Gradle 需要重新生成
> NeoForge 工件（实测约 15 秒，网络慢时更久）；之后再跑就很快。
> 批量构建依赖项目里已有的 `nomods1` 模块来解析 NeoForge classpath；如果它被删了，
> 先运行一次 `python generate.py --count 10`。

## 百万级空模组（快速路径）

`massbuild.py` 每个模组都要写 `.java`、跑 javac、写 `.class`，百万级大约要 1.5 小时，
而且 100 万个源文件放在一个目录里会越来越慢。空模组除了编号之外完全一样，所以
[fast_empty.py](fast_empty.py) 用另一种做法：

1. 只为 1/2/3/4/5/6/7 位编号各编译**一个** class 模板（7 个）；
2. 其余模组按相同位数做**等长字节替换**（包名、类名、modid 都只换数字），
   得到的 class 与 javac 编译结果逐字节一致（已对比 1..6 位样本）；
3. 直接在内存里生成 jar 并并行写出，**不产生任何源码/class 临时文件**。

```powershell
# 100 万个空模组，编号 1..1000000 -> dist-empty\
python fast_empty.py --count 1000000 --out dist-empty --force

# 想和 hello 版共存（不重复 modid）就从 10001 开始
python fast_empty.py --count 1000000 --start 10001 --out dist-empty --force
```

实测 100 万个空模组：**820 秒（13.7 分钟）**，1000000 个 jar、1315 MB，8 个并行写入线程。
第 1 个、第 9 个、第 12345 个、第 123456 个、第 999999 个、第 1000000 个都已抽查，
条目和 modid 正确。

`massbuild.py` 也加了 `--batch-size`（默认 10000）和 `--jobs`（默认 6），按批次渲染/编译/打包，
每批结束立刻删除该批临时文件；即使不用快速路径，百万级也不会再把 100 万个文件堆在一起。

## 加快服务器启动：把几千个模组分片打包

**问题 1（启动慢）**：NeoForge 启动时会为每个 mod **jar 文件**建立一个 JPMS 模块，
然后构建 module layer。4097 个 jar 时，卡在 JDK 的
`java.lang.module.Resolver.makeGraph`，10 分钟都进不到真正的模组加载阶段。

**问题 2（合并后进不去服务器）**：把所有模组塞进**一个** jar 虽然启动快，但
NeoForge 会把同一个 jar 里所有模组的 id / 版本用逗号拼成资源包名和版本：

```text
name    = "mod/nomods1,nomods2,..."
version = "1.0.0-neoforge1.21.1,1.0.0-neoforge1.21.1,..."
```

这两串会随 `select_known_packs` 发给客户端，而 Minecraft 的 `Utf8String` 上限是
**32767 字符**。4000 个模组时 name 有 42896 字符，于是每个客户端都会在登录时被踢：

```text
EncoderException: Failed to encode packet 'clientbound/minecraft:select_known_packs'
Caused by: String too big (was 42896 characters, max 32767)
```

**解决**：分片打包，默认 **1000 个模组一个 jar**（`--chunk-size` 可调）。这样：

- 模块数从几千个降到几个，启动仍然很快；
- 每个分片的 name / version 字符串只有约 1 万 / 2.1 万字符，安全低于 32767。

模组本身一个都没少、没改：modid、类、网络通道、作者、版本全部不变，FML 仍然会
为每个 mod 创建容器并加载（实测 4000 个 mod → 4001 个容器，含 NeoForge 自己）。

```powershell
# 默认每个 jar 1000 个模组，输出 nomods-bundle-01.jar .. -04.jar
python merge_nomods.py --mods E:\server1211\mods-original `
  --output E:\server1211\mods\nomods-bundle.jar --limit 4000

# 想调整每片大小
python merge_nomods.py --mods ... --output ... --limit 4000 --chunk-size 800

# 单个 jar（只适合 < 约 1450 个模组，否则登录必崩）
python merge_nomods.py --mods ... --output ... --limit 1200 --chunk-size 0
```

在 `E:\server1211` 上的实测：

| 方式 | 启动到 `Done` | 能否进服 |
| --- | --- | --- |
| 4097 个 jar | 10+ 分钟仍卡在模块解析 | 能（但等不起） |
| 4000 个模组合并成 1 个 jar | 约 39 秒 | **不能**，`select_known_packs` 编码失败 |
| 4000 个模组按 1000 分片成 4 个 jar | 约 30-40 秒 | 能 |

注意：

- 分片后日志里的 `Mod List:` 只会显示每个 jar 的第一个 mod（FML 的显示方式），
  实际加载数量看 `logs/debug.log` 里的 `Creating FMLModContainer instance` 行数；
- 原始 jar 建议原样保留在 `mods-original\`（不会被加载），要还原就删掉分片、
  把目录改名回 `mods`；
- 客户端如果也用合并包，同样要分片（或者继续用原来的单 jar），并且**不要**和
  单独的 nomodsN jar 同时启用，否则 mod id 重复；
- 以后新增真模组直接放进 `mods\` 即可，和分片包共存。

## 生成器用法

```text
python generate.py                       # 确保 nomods1 .. nomods10 存在且是最新模板
python generate.py --count 30            # 一直生成到 nomods30
python generate.py --start 11 --count 10 # 只生成 nomods11 .. nomods20
python generate.py --count 5 --prune     # 同时删除范围外的、由本工具生成的模组
python generate.py --dry-run             # 只打印将要做什么，不改文件
```

生成器会重写 `settings.gradle` 里的 `include` 列表，所以新增模组后不需要手动改 Gradle 配置。

### 改模组元数据

模组版本、作者、描述、NeoForge 版本、包名前缀等都写在根目录的 `gradle.properties` 里：

```properties
mod_version=1.0.0-neoforge1.21.1
mod_authors=mingzi_94387355, deepseek
mod_license=MIT
mod_description=Only registers a network channel. Does nothing else.
nomods_package_root=com.chapple
nomods_protocol_version=1
neo_version=21.1.233
```

改完**重新运行 `python generate.py`**，生成器会把新值写进每个模组的 `neoforge.mods.toml`
和 Java 源码里（`build.gradle` 在构建时直接读这些属性）。

> 生成出来的文件开头都有 `Generated by generate.py` 标记，下次运行会被覆盖；
> 要加自己的功能，建议先复制一份模组目录再改。

## 目录结构

```text
nomods-neoforge1.21.1\
├─ generate.py                 # 生成器：写出 nomodsN 源码目录（几十个用这个）
├─ massbuild.py                # 批量构建器：直接产出上万个 jar，不留中间产物
├─ fast_empty.py               # 空模组快速构建：模板 class + 等长替换，百万级
├─ merge_nomods.py             # 把上千个 jar 合并成一个 bundle jar，加快服务器启动
├─ build.ps1                   # 找 JDK 21 + Gradle 构建 + 收集 jar 到 dist
├─ gradle.properties           # 共享的版本号 / 作者 / 描述
├─ settings.gradle             # 由 generate.py 维护 include 列表
├─ build.gradle                # 根构建脚本：插件版本 + maven 仓库 + classpath 导出任务
├─ gradlew / gradlew.bat       # Gradle wrapper（9.2.1）
├─ nomods1\
│  ├─ build.gradle             # 单个模组的构建脚本（net.neoforged.moddev）
│  └─ src\main\
│     ├─ java\com\chapple\nomods1\
│     │  ├─ NoMods1.java       # @Mod 入口，注册通道
│     │  └─ HelloPayload.java  # hello payload + 处理器
│     └─ resources\META-INF\neoforge.mods.toml
├─ nomods2\ ...                # 结构完全相同
├─ dist\                       # hello 版 10000 个 jar
└─ dist-empty\                 # 空模组 100 万个 jar（nomods1..1000000）
```

## 关于"网络通道"和 hello 包

NeoForge 1.21.1 已经不再使用旧版的 `SimpleChannel`/`ChannelBuilder`，通道是**注册 payload 时隐式创建**的：

```java
final PayloadRegistrar registrar = event.registrar(PROTOCOL_VERSION).optional();
registrar.playBidirectional(HelloPayload.TYPE, HelloPayload.STREAM_CODEC, HelloPayload::handle);
```

- `HelloPayload` 里只有一个字符串，codec 用 `ByteBufCodecs.STRING_UTF8`；
- 玩家登录时（`PlayerEvent.PlayerLoggedInEvent`），服务端发一句 `hello from nomodsN`：

  ```java
  if (player.connection.hasChannel(HelloPayload.TYPE)) {
      PacketDistributor.sendToPlayer(player, new HelloPayload("hello from " + MOD_ID));
  }
  ```

  先判断 `hasChannel`，客户端没装这个模组时就不发，避免对方无法解码 payload 被踢；
- 客户端 `handle(...)` 里打印 `nomodsN received hello packet: hello from nomodsN`；
- `optional()` 表示这条通道是可选的：即使服务器/客户端只有一边装了这个模组，也不会因为通道不匹配而断开。

也就是说：模组会出现在模组列表里、参与通道握手，并在进服时收发一条 hello 消息，除此之外不做任何事情。

## 常见问题

**Gradle 报 "Unsupported class file major version" / 无法启动**
说明 Gradle 用到了 JDK 8/17 等旧版本。设置 `JAVA_HOME` 指向 JDK 21，或直接用 `.\build.ps1`。

**首次构建很慢 / 占空间**
ModDevGradle 需要下载并处理 NeoForge（每个模块的 `build\moddev` 下会有几十 MB 的中间产物）。
之后再构建会走缓存。若只想构建某一个模组：

```powershell
.\gradlew.bat :nomods3:build
```

**只想删掉生成的模组重新来**
`python generate.py --count 0 --prune` 会删除所有由本工具生成的 `nomodsN` 目录（不会动
`--prune` 不认识的目录）。

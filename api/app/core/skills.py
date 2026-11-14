"""The one canonical skill vocabulary and normaliser (AD-8).

* A ``skill_id`` is a stable lower-kebab slug. It is never reused or removed; a rename is an
  extra alias on the existing id.
* :data:`VOCAB_VERSION` changes whenever the vocabulary changes, and every stored skill set
  records the version it was normalised with.
* Aliases match case-insensitively on token boundaries: a match is never preceded by a letter
  or digit and never followed by a letter, digit, ``+`` or ``#``. So "Java" does not match in
  "JavaScript", "R" does not match inside words, and "C" does not match "C++" or "C#".
  Alternatives are tried longest first, so "React Native" is ``react-native``, not ``react``.
* The ambiguous one-word names "Go", "R" and "C" match only case-sensitively, never when
  followed by ``&``, ``-`` or ``'`` (R&D, C-suite) or preceded by "grade " / "class "
  (Grade C), and never inside a longer alias (Objective-C). "golang", "c language" etc. stay
  case-insensitive.
* No other module keeps an alias table: ``profile`` and ``listings`` both call this module.

Pure Python, data in the module, no I/O.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

VOCAB_VERSION = "2026.11.2"

# Alias key -> the exact (case-sensitive) text that counts as a mention in prose.
_AMBIGUOUS = {"go": "Go", "r": "R", "c": "C"}


@dataclass(frozen=True)
class Skill:
    id: str
    label: str
    aliases: tuple[str, ...] = ()


def _s(skill_id: str, label: str, *aliases: str) -> Skill:
    return Skill(skill_id, label, aliases)


# fmt: off
_VOCABULARY: tuple[Skill, ...] = (
    # ------------------------------------------------------------------ languages
    _s("python", "Python", "python3", "python 3"),
    _s("java", "Java", "core java", "java se", "java ee", "j2ee"),
    _s("javascript", "JavaScript", "js", "java script", "es6", "es2015", "es2016", "es2017",
       "es2018", "es2019", "es2020", "ecmascript", "vanilla js"),
    _s("typescript", "TypeScript", "ts"),
    _s("c", "C", "c language", "c programming", "ansi c"),
    _s("cpp", "C++", "c++", "cpp", "c plus plus", "c++11", "c++14", "c++17", "c++20"),
    _s("csharp", "C#", "c#", "c sharp", "csharp"),
    _s("go", "Go", "golang", "go lang"),
    _s("rust", "Rust", "rustlang"),
    _s("kotlin", "Kotlin"),
    _s("swift", "Swift"),
    _s("objective-c", "Objective-C", "objective c", "objc", "obj-c"),
    _s("ruby", "Ruby"),
    _s("php", "PHP", "php7", "php8"),
    _s("scala", "Scala"),
    _s("r", "R", "r programming", "r language", "rstudio"),
    _s("matlab", "MATLAB"),
    _s("perl", "Perl"),
    _s("dart", "Dart"),
    _s("haskell", "Haskell"),
    _s("elixir", "Elixir"),
    _s("lua", "Lua"),
    _s("julia", "Julia"),
    _s("sql", "SQL", "structured query language"),
    _s("bash", "Bash", "shell scripting", "shell script", "bash scripting"),
    _s("powershell", "PowerShell"),
    _s("html", "HTML", "html5"),
    _s("css", "CSS", "css3"),
    _s("sass", "Sass", "scss"),
    _s("solidity", "Solidity"),
    _s("assembly", "Assembly", "assembly language", "x86 assembly", "asm"),
    _s("verilog", "Verilog", "systemverilog"),
    _s("vhdl", "VHDL"),
    # ------------------------------------------------------------------ web / frontend
    _s("react", "React", "react.js", "reactjs", "react js"),
    _s("react-native", "React Native", "react-native"),
    _s("nextjs", "Next.js", "next.js", "nextjs", "next js"),
    _s("angular", "Angular", "angular.js", "angularjs", "angular js"),
    _s("vue", "Vue.js", "vue", "vue.js", "vuejs", "vue js"),
    _s("nuxt", "Nuxt", "nuxt.js", "nuxtjs"),
    _s("svelte", "Svelte", "sveltekit"),
    _s("jquery", "jQuery"),
    _s("redux", "Redux", "redux toolkit"),
    _s("tailwind-css", "Tailwind CSS", "tailwind", "tailwindcss"),
    _s("bootstrap", "Bootstrap"),
    _s("material-ui", "Material UI", "mui", "material-ui"),
    _s("webpack", "Webpack"),
    _s("vite", "Vite"),
    _s("graphql", "GraphQL"),
    _s("rest-api", "REST APIs", "restful", "rest api", "rest apis", "restful api",
       "restful apis", "restful services"),
    _s("websockets", "WebSockets", "websocket", "socket.io"),
    _s("flutter", "Flutter"),
    _s("android", "Android", "android development", "android sdk"),
    _s("ios", "iOS", "ios development"),
    _s("swiftui", "SwiftUI"),
    _s("jetpack-compose", "Jetpack Compose"),
    # ------------------------------------------------------------------ backend
    _s("nodejs", "Node.js", "node.js", "nodejs", "node js"),
    _s("express", "Express.js", "expressjs", "express js"),
    _s("nestjs", "NestJS", "nest.js", "nestjs"),
    _s("django", "Django", "django rest framework", "drf"),
    _s("flask", "Flask"),
    _s("fastapi", "FastAPI", "fast api"),
    _s("spring-framework", "Spring Framework", "spring mvc"),
    _s("spring-boot", "Spring Boot", "springboot"),
    _s("hibernate", "Hibernate", "jpa"),
    _s("dotnet", ".NET", ".net", "dotnet", ".net core", "dot net"),
    _s("asp-net", "ASP.NET", "asp.net", "asp.net core", "asp.net mvc"),
    _s("ruby-on-rails", "Ruby on Rails", "rails", "ror"),
    _s("laravel", "Laravel"),
    _s("microservices", "Microservices", "microservice", "microservice architecture"),
    _s("grpc", "gRPC"),
    _s("kafka", "Apache Kafka", "kafka", "apache kafka"),
    _s("rabbitmq", "RabbitMQ"),
    _s("celery", "Celery"),
    # ------------------------------------------------------------------ databases
    _s("postgresql", "PostgreSQL", "postgres", "postgresql", "psql"),
    _s("mysql", "MySQL"),
    _s("sqlite", "SQLite"),
    _s("oracle-database", "Oracle Database", "oracle db", "oracle sql", "pl/sql", "plsql"),
    _s("sql-server", "Microsoft SQL Server", "sql server", "mssql", "ms sql", "t-sql", "tsql"),
    _s("mongodb", "MongoDB", "mongo", "mongo db"),
    _s("redis", "Redis"),
    _s("cassandra", "Apache Cassandra", "cassandra"),
    _s("dynamodb", "DynamoDB", "dynamo db"),
    _s("elasticsearch", "Elasticsearch", "elastic search", "elk", "opensearch"),
    _s("firebase", "Firebase", "firestore"),
    _s("supabase", "Supabase"),
    _s("neo4j", "Neo4j"),
    _s("snowflake", "Snowflake"),
    _s("bigquery", "BigQuery", "big query"),
    _s("sqlalchemy", "SQLAlchemy"),
    _s("prisma", "Prisma"),
    # ------------------------------------------------------------------ cloud / devops
    _s("aws", "AWS", "amazon web services"),
    _s("aws-ec2", "AWS EC2", "ec2"),
    _s("aws-s3", "AWS S3", "s3", "amazon s3"),
    _s("aws-lambda", "AWS Lambda", "lambda functions"),
    _s("azure", "Microsoft Azure", "azure", "ms azure"),
    _s("gcp", "Google Cloud", "gcp", "google cloud platform", "google cloud"),
    _s("heroku", "Heroku"),
    _s("vercel", "Vercel"),
    _s("docker", "Docker", "dockerfile", "docker compose", "docker-compose"),
    _s("kubernetes", "Kubernetes", "k8s"),
    _s("terraform", "Terraform"),
    _s("ansible", "Ansible"),
    _s("jenkins", "Jenkins"),
    _s("github-actions", "GitHub Actions"),
    _s("gitlab-ci", "GitLab CI", "gitlab ci/cd"),
    _s("ci-cd", "CI/CD", "ci/cd", "ci cd", "cicd", "continuous integration",
       "continuous delivery", "continuous deployment"),
    _s("devops", "DevOps"),
    _s("linux", "Linux", "ubuntu", "unix", "red hat", "centos", "debian"),
    _s("nginx", "Nginx"),
    _s("apache-http-server", "Apache HTTP Server", "apache httpd", "apache server"),
    _s("prometheus", "Prometheus"),
    _s("grafana", "Grafana"),
    _s("serverless", "Serverless", "serverless framework"),
    _s("cloud-computing", "Cloud Computing"),
    # ------------------------------------------------------------------ tools
    _s("git", "Git", "version control"),
    _s("github", "GitHub"),
    _s("gitlab", "GitLab"),
    _s("bitbucket", "Bitbucket"),
    _s("jira", "Jira"),
    _s("confluence", "Confluence"),
    _s("postman", "Postman"),
    _s("figma", "Figma"),
    _s("vs-code", "VS Code", "visual studio code", "vscode"),
    _s("intellij-idea", "IntelliJ IDEA", "intellij"),
    _s("microsoft-excel", "Microsoft Excel", "ms excel", "advanced excel", "excel sheets"),
    _s("power-bi", "Power BI", "powerbi"),
    _s("tableau", "Tableau"),
    _s("jupyter", "Jupyter", "jupyter notebook", "jupyter notebooks", "jupyterlab"),
    _s("selenium", "Selenium", "selenium webdriver"),
    _s("cypress", "Cypress"),
    _s("playwright", "Playwright"),
    _s("jest", "Jest"),
    _s("junit", "JUnit"),
    _s("pytest", "pytest"),
    _s("maven", "Maven"),
    _s("gradle", "Gradle"),
    _s("npm", "npm"),
    _s("unity", "Unity", "unity3d"),
    _s("unreal-engine", "Unreal Engine"),
    _s("autocad", "AutoCAD"),
    _s("latex", "LaTeX"),
    # ------------------------------------------------------------------ data / ML
    _s("machine-learning", "Machine Learning", "ml"),
    _s("deep-learning", "Deep Learning", "dl"),
    _s("artificial-intelligence", "Artificial Intelligence", "ai"),
    _s("nlp", "Natural Language Processing", "nlp", "natural language processing"),
    _s("computer-vision", "Computer Vision", "opencv"),
    _s("generative-ai", "Generative AI", "genai", "gen ai", "generative ai"),
    _s("llm", "Large Language Models", "llm", "llms", "large language models",
       "large language model"),
    _s("prompt-engineering", "Prompt Engineering"),
    _s("langchain", "LangChain"),
    _s("tensorflow", "TensorFlow"),
    _s("keras", "Keras"),
    _s("pytorch", "PyTorch", "torch"),
    _s("scikit-learn", "scikit-learn", "sklearn", "scikit learn"),
    _s("pandas", "pandas"),
    _s("numpy", "NumPy"),
    _s("scipy", "SciPy"),
    _s("matplotlib", "Matplotlib"),
    _s("seaborn", "Seaborn"),
    _s("hugging-face", "Hugging Face", "huggingface"),
    _s("xgboost", "XGBoost"),
    _s("data-analysis", "Data Analysis", "data analytics"),
    _s("data-science", "Data Science"),
    _s("data-visualization", "Data Visualization", "data visualisation"),
    _s("data-engineering", "Data Engineering"),
    _s("statistics", "Statistics", "statistical analysis"),
    _s("big-data", "Big Data"),
    _s("apache-spark", "Apache Spark", "spark", "pyspark"),
    _s("hadoop", "Hadoop", "hdfs", "mapreduce"),
    _s("airflow", "Apache Airflow", "airflow"),
    _s("etl", "ETL", "elt"),
    _s("mlops", "MLOps"),
    _s("reinforcement-learning", "Reinforcement Learning"),
    # ------------------------------------------------------------------ CS fundamentals
    _s("data-structures", "Data Structures", "data structure", "dsa",
       "data structures and algorithms"),
    _s("algorithms", "Algorithms", "algorithm design"),
    _s("oop", "Object-Oriented Programming", "oop", "oops", "object oriented programming",
       "object-oriented programming", "object oriented design"),
    _s("operating-systems", "Operating Systems", "operating system"),
    _s("computer-networks", "Computer Networks", "computer networking", "networking",
       "tcp/ip"),
    _s("dbms", "Database Management Systems", "dbms", "database management",
       "database management systems", "rdbms"),
    _s("system-design", "System Design", "systems design", "low level design",
       "high level design"),
    _s("distributed-systems", "Distributed Systems"),
    _s("design-patterns", "Design Patterns"),
    _s("functional-programming", "Functional Programming"),
    _s("concurrency", "Concurrency", "multithreading", "multi-threading"),
    _s("compilers", "Compiler Design", "compilers", "compiler design"),
    _s("computer-architecture", "Computer Architecture", "computer organization"),
    _s("cybersecurity", "Cybersecurity", "cyber security", "information security",
       "network security"),
    _s("cryptography", "Cryptography"),
    _s("blockchain", "Blockchain"),
    _s("embedded-systems", "Embedded Systems", "embedded c"),
    _s("iot", "Internet of Things", "iot", "internet of things"),
    _s("unit-testing", "Unit Testing", "unit tests", "tdd", "test driven development"),
    _s("software-testing", "Software Testing", "manual testing", "qa testing",
       "quality assurance"),
    _s("agile", "Agile", "agile methodology", "scrum", "kanban"),
    _s("sdlc", "SDLC", "software development life cycle"),
    _s("ui-ux-design", "UI/UX Design", "ui/ux", "ux design", "ui design", "user experience"),
    _s("competitive-programming", "Competitive Programming", "leetcode", "codeforces",
       "codechef"),
    # ------------------------------------------------------------------ soft skills
    _s("communication", "Communication", "communication skills", "verbal communication",
       "written communication"),
    _s("teamwork", "Teamwork", "team player", "collaboration", "team work"),
    _s("leadership", "Leadership", "team leadership"),
    _s("problem-solving", "Problem Solving", "problem-solving", "analytical skills"),
    _s("critical-thinking", "Critical Thinking"),
    _s("time-management", "Time Management"),
    _s("project-management", "Project Management"),
    _s("public-speaking", "Public Speaking", "presentation skills"),
    _s("adaptability", "Adaptability"),
    _s("creativity", "Creativity"),
    _s("attention-to-detail", "Attention to Detail"),
    _s("mentoring", "Mentoring", "mentorship"),
    _s("technical-writing", "Technical Writing", "documentation"),
)
# fmt: on

_SPACES = re.compile(r"\s+")


def _key(term: str) -> str:
    """Case-folded, whitespace-collapsed form used for exact lookups."""
    return _SPACES.sub(" ", term).strip().casefold()


def _build() -> tuple[dict[str, Skill], dict[str, str], re.Pattern[str]]:
    by_id: dict[str, Skill] = {}
    alias_to_id: dict[str, str] = {}
    for skill in _VOCABULARY:
        if skill.id in by_id:
            raise ValueError(f"duplicate skill id {skill.id!r}")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill.id):
            raise ValueError(f"skill id {skill.id!r} is not a lower-kebab slug")
        by_id[skill.id] = skill
        for term in (skill.id, skill.label, *skill.aliases):
            key = _key(term)
            owner = alias_to_id.setdefault(key, skill.id)
            if owner != skill.id:
                raise ValueError(f"alias {term!r} belongs to {owner!r} and {skill.id!r}")
    # Longest first, so "react native" wins over "react" and "c++" over "c".
    phrases = sorted((k for k in alias_to_id if k not in _AMBIGUOUS), key=lambda k: (-len(k), k))
    body = "|".join(r"\s+".join(re.escape(part) for part in p.split(" ")) for p in phrases)
    pattern = re.compile(rf"(?<![0-9a-z])(?:{body})(?![0-9a-z+#])", re.IGNORECASE)
    return by_id, alias_to_id, pattern


_BY_ID, _ALIAS_TO_ID, _PATTERN = _build()
_AMBIGUOUS_PATTERN = re.compile(
    r"(?<![0-9A-Za-z\-])(?<![Gg]rade )(?<![Cc]lass )(?:"
    + "|".join(re.escape(t) for t in _AMBIGUOUS.values())
    + r")(?![0-9A-Za-z+#&'\-])"
)


def all_skills() -> tuple[Skill, ...]:
    """Every canonical skill, in vocabulary order."""
    return _VOCABULARY


def is_skill_id(skill_id: str) -> bool:
    return skill_id in _BY_ID


def label(skill_id: str) -> str:
    """The display label of ``skill_id``; the id itself when it is not in this vocabulary."""
    skill = _BY_ID.get(skill_id)
    return skill.label if skill is not None else skill_id


def lookup(term: str) -> str | None:
    """The ``skill_id`` whose id, label or alias equals ``term`` (case-insensitive,
    whitespace-collapsed), else None. ``lookup("JS") == "javascript"``."""
    return _ALIAS_TO_ID.get(_key(term))


def normalize(text: str) -> set[str]:
    """Every canonical skill mentioned in ``text``."""
    found: set[str] = set()
    covered: list[tuple[int, int]] = []
    for match in _PATTERN.finditer(text):
        skill_id = _ALIAS_TO_ID.get(_key(match.group(0)))
        if skill_id is not None:
            found.add(skill_id)
            covered.append(match.span())
    for match in _AMBIGUOUS_PATTERN.finditer(text):
        start = match.start()
        if not any(a <= start < b for a, b in covered):
            found.add(_ALIAS_TO_ID[match.group(0).casefold()])
    return found


def suggest(query: str, limit: int = 20) -> list[Skill]:
    """Autocomplete: skills whose label or alias starts with ``query`` first, then those
    containing it; each group in label order. At most ``limit`` results."""
    q = _key(query)
    if not q or limit < 1:
        return []
    prefix: list[Skill] = []
    contains: list[Skill] = []
    for skill in sorted(_VOCABULARY, key=lambda s: s.label.casefold()):
        terms = [_key(t) for t in (skill.label, skill.id, *skill.aliases)]
        if any(t.startswith(q) for t in terms):
            prefix.append(skill)
        elif any(q in t for t in terms):
            contains.append(skill)
    return (prefix + contains)[:limit]


def sorted_ids(ids: Iterable[str]) -> list[str]:
    """``ids`` de-duplicated and ordered by label (stable display and storage order)."""
    return sorted(set(ids), key=lambda i: (label(i).casefold(), i))

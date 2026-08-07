with open("app/pipeline/resolver.py", "r") as f:
    content = f.read()

# Replace the provider branching in resolve_issues
import re
new_content = re.sub(
    r'    if provider == "openai":.*?elif provider == "gemini":\s*try:',
    r'''    try:''',
    content,
    flags=re.DOTALL
)

with open("app/pipeline/resolver.py", "w") as f:
    f.write(new_content)

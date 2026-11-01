"""Read upstream dependency pins without executing setup.py."""
import ast
from pathlib import Path

tree = ast.parse(Path('setup.py').read_text())
requirements = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'requirements'
                            for target in node.targets))
Path('/tmp/pybossa-requirements.txt').write_text('\n'.join(requirements) + '\n')

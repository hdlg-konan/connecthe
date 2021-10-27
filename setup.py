from setuptools import setup, find_packages

with open('requirements.txt') as f:
	install_requires = f.read().strip().split('\n')

# get version from __version__ variable in connecthe/__init__.py
from connecthe import __version__ as version

setup(
	name='connecthe',
	version=version,
	description='Custom Fields for Connecthe',
	author='Fabien MARCH',
	author_email='admin@leconnecthe.com',
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)

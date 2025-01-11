from setuptools import setup, find_packages

# Read the requirements from the requirements.txt file
with open('requirements.txt') as f:
    requirements = f.read().splitlines()
    requirements = [r for r in requirements if r and not r.startswith('#')]

setup(
    name='srst-nrkup',
    version='1.0.0',
    description='NRK Download and Telegram Bot Integration',
    author='Yuri Bochkarev',
    author_email='baltazar.bz@gmail.com',
    url='https://github.com/balta2ar/srst-nrkup',
    packages=find_packages(),
    include_package_data=True,
    #py_modules=['nrkup', 'episode', 'nrkup_bot'],
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'srst-nrkup-bot=nrkup.nrkup_bot:main',
        ],
    },
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
)

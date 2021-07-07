## Installation

Any command `in code blocks` is meant to be executed from a Mac/Linux terminal or Windows command prompt.

_Note for Windows users:_ if you want to use the Windows Subsystem for Linux (WSL), go ahead and [install it now](https://docs.microsoft.com/en-us/windows/wsl/install-win10)
   - After it's installed, launch your chosen Linux subsystem
   - Follow the Linux instructions below from within your terminal, except for VSCode. Any VSCode installation happens in Windows, not the Linux subsystem.

1. Install [VSCode](https://code.visualstudio.com/docs/setup/setup-overview)
2. Install VSCode Extensions
   - [Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python)
   - If you're using the WSL
     - Install [Remote - WSL](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-wsl)
3. Install [Python 3.8](https://www.python.org/downloads/release/python-380/)
   - Linux: Refer to your distro documentation
   - [Mac installer](https://www.python.org/ftp/python/3.8.0/python-3.8.0-macosx10.9.pkg)
   - [Windows installer](https://www.python.org/ftp/python/3.8.0/python-3.8.0-amd64.exe)
4. [Setup Brownie](https://github.com/eth-brownie/brownie)
   - `python3 -m pip install --user pipx`
     - Note, if get you an error to the effect of python3 not being installed or recognized, run `python --version`, if it returns back something like `Python 3.8.x` then just replace `python3` with `python` for all python commands in these instructions
   - `python3 -m pipx ensurepath`
   - `pipx install eth-brownie`
     - If you're on Windows (pure Windows, not WSL), you'll need to install the [C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) before executing this
6. Install [Ganache](https://github.com/trufflesuite/ganache-cli)
   - `npm install -g ganache-cli@6.12.1`
9. Setup an account on [Etherscan](https://etherscan.io) and create an API key
   - Set `ETHERSCAN_TOKEN` environment variable to this key's value
     - Windows: `setx ETHERSCAN_TOKEN yourtokenvalue`
     - Mac/Linux: `echo "export ETHERSCAN_TOKEN=\"yourtokenvalue\"" | sudo tee -a ~/.bash_profile`
10. Setup an account on [Infura](https://infura.io) and create an API key
    - Set `WEB3_INFURA_PROJECT_ID` environment variable to this key's value
      - Windows: `setx WEB3_INFURA_PROJECT_ID yourtokenvalue`
      - Mac/Linux: `echo "export WEB3_INFURA_PROJECT_ID=\"yourtokenvalue\"" | sudo tee -a ~/.bash_profile`
11. Close & re-open your terminal before proceeding (to get the new environment variable values)
12. If you don't have git yet, go [set it up](https://docs.github.com/en/free-pro-team@latest/github/getting-started-with-github/set-up-git)
13. Pull the repository from GitHub and install its dependencies
    - `git clone https://github.com/yearn/yearn-vaults`
    - `cd https://github.com/listonjesse/coordinape_scripts`
    - `pip3 install -r requirements.txt`

16. Launch VSCode
    - If you're in Windows using WSL, type `code .` to launch VSCode
17. Lastly, you'll want to add .vscode to to your global .gitignore
    - Use a terminal on Mac / Linux, use Git Bash on Windows
    - `touch ~/.gitignore_global`
    - use your favorite editor and add `.vscode/` to the ignore file
      - Using vi:
        - `vi ~/.gitignore_global`
        - copy `.vscode/` and hit `p` in vi
        - type `:x` and hit enter
    - `git config --global core.excludesfile ~/.gitignore_global`
18. Congratulations! You're all set up.
    - Use `git pull` to stay up to date with any changes made to the source code

# Running coordinape_scripts
## Running from terminal
`brownie run coordinape_disperse.py <function_name>`

## Running from VSCode
Go to [scripts/coordinape_disperse.py](coordinape_disperse.py), hit the debug button in VSCode and select **Python: Current File** as the target. This will run whatever function is called at the very end of the coordinape_disperse.py file. Feel free to change this when debugging with VSCode.

# Adding a new epoch
Add mapping of epoch to USD amount in DEFAULT_USD_REWARD_DICT found in [configuration.py](scripts/configuration.py)

In [coordinape_disperse.py](scripts/coordinape_disperse.py), add a function for your circle's epoch. Choose between various dispersement options defined in [coordinape_enums.py](scripts/coordinape_enums.py)
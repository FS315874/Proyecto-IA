from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    name: str
    url: str


@dataclass(frozen=True)
class Application:
    name: str
    executable_names: tuple[str, ...]
    windows_paths: tuple[str, ...]


SUPPORTED_SITES: dict[str, Site] = {
    "youtube": Site(name="YouTube", url="https://www.youtube.com/"),
    "google": Site(name="Google", url="https://www.google.com/"),
    "github": Site(name="GitHub", url="https://github.com/"),
}


SUPPORTED_APPLICATIONS: dict[str, Application] = {
    "chrome": Application(
        name="Google Chrome",
        executable_names=("chrome.exe", "chrome"),
        windows_paths=(
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        ),
    ),
    "vscode": Application(
        name="Visual Studio Code",
        executable_names=("code.exe", "code"),
        windows_paths=(
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"%ProgramFiles%\Microsoft VS Code\Code.exe",
            r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
        ),
    ),
    "calculator": Application(
        name="Calculadora",
        executable_names=("calc.exe", "calc"),
        windows_paths=(r"%WINDIR%\System32\calc.exe",),
    ),
}


APPLICATION_ALIASES: dict[str, str] = {
    "chrome": "chrome",
    "vscode": "vscode",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "calculadora": "calculator",
    "calculator": "calculator",
}

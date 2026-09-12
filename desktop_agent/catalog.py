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
    process_names: tuple[str, ...]
    launch_arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserApplication:
    name: str
    executable_names: tuple[str, ...]
    windows_paths: tuple[str, ...]


SUPPORTED_SITES: dict[str, Site] = {
    "youtube": Site(name="YouTube", url="https://www.youtube.com/"),
    "google": Site(name="Google", url="https://www.google.com/"),
    "github": Site(name="GitHub", url="https://github.com/"),
    "spotify": Site(name="Spotify Web", url="https://open.spotify.com/"),
}


SITE_ALIASES: dict[str, str] = {
    "youtube": "youtube",
    "google": "google",
    "github": "github",
    "spotify": "spotify",
    "spotify web": "spotify",
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
        process_names=("chrome.exe",),
    ),
    "vscode": Application(
        name="Visual Studio Code",
        executable_names=("code.exe", "code"),
        windows_paths=(
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"%ProgramFiles%\Microsoft VS Code\Code.exe",
            r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
        ),
        process_names=("Code.exe",),
    ),
    "calculator": Application(
        name="Calculadora",
        executable_names=("calc.exe", "calc"),
        windows_paths=(r"%WINDIR%\System32\calc.exe",),
        process_names=("CalculatorApp.exe", "Calculator.exe", "calc.exe"),
    ),
    "spotify": Application(
        name="Spotify",
        executable_names=("Spotify.exe", "spotify"),
        windows_paths=(
            r"%APPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
        ),
        process_names=("Spotify.exe",),
    ),
    "steam": Application(
        name="Steam",
        executable_names=("steam.exe", "steam"),
        windows_paths=(
            r"%ProgramFiles(x86)%\Steam\steam.exe",
            r"%ProgramFiles%\Steam\steam.exe",
        ),
        process_names=("steam.exe",),
    ),
    "voicemeeter": Application(
        name="VoiceMeeter Banana",
        executable_names=("voicemeeterpro.exe",),
        windows_paths=(
            r"%ProgramFiles(x86)%\VB\Voicemeeter\voicemeeterpro.exe",
            r"%ProgramFiles%\VB\Voicemeeter\voicemeeterpro.exe",
        ),
        process_names=("voicemeeterpro.exe",),
    ),
    "league_of_legends": Application(
        name="League of Legends",
        executable_names=("RiotClientServices.exe",),
        windows_paths=(r"C:\Riot Games\Riot Client\RiotClientServices.exe",),
        process_names=(
            "LeagueClient.exe",
            "LeagueClientUx.exe",
        ),
        launch_arguments=("--launch-product=league_of_legends", "--launch-patchline=live"),
    ),
    "god_of_war_ragnarok": Application(
        name="God of War Ragnarök",
        executable_names=("GoWR.exe",),
        windows_paths=(
            r"C:\God of War Ragnarok\GoWR.exe",
            r"%ProgramFiles(x86)%\Steam\steamapps\common\God of War Ragnarok\GoWR.exe",
            r"%ProgramFiles%\Steam\steamapps\common\God of War Ragnarok\GoWR.exe",
        ),
        process_names=("GoWR.exe",),
    ),
}


APPLICATION_ALIASES: dict[str, str] = {
    "chrome": "chrome",
    "vscode": "vscode",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "calculadora": "calculator",
    "calculator": "calculator",
    "spotify app": "spotify",
    "spotify escritorio": "spotify",
    "steam": "steam",
    "voicemeeter": "voicemeeter",
    "voice meeter": "voicemeeter",
    "voicemeeter banana": "voicemeeter",
    "voice meeter banana": "voicemeeter",
    "league": "league_of_legends",
    "league of legends": "league_of_legends",
    "lol": "league_of_legends",
    "god of war": "god_of_war_ragnarok",
    "god of war ragnarok": "god_of_war_ragnarok",
}


SUPPORTED_BROWSERS: dict[str, BrowserApplication] = {
    "chrome": BrowserApplication(
        name="Google Chrome",
        executable_names=("chrome.exe", "chrome"),
        windows_paths=SUPPORTED_APPLICATIONS["chrome"].windows_paths,
    ),
    "opera_gx": BrowserApplication(
        name="Opera GX",
        executable_names=("opera.exe", "launcher.exe"),
        windows_paths=(
            r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe",
            r"%LOCALAPPDATA%\Programs\Opera GX\launcher.exe",
            r"%ProgramFiles%\Opera GX\launcher.exe",
            r"%ProgramFiles(x86)%\Opera GX\launcher.exe",
        ),
    ),
}

#!/usr/bin/env python3
"""
Установщик интеграции jpeg2eps для macOS:
1. Создаёт глобальную CLI-команду `~/.local/bin/jpeg2eps`
2. Создаёт Быстрое действие Finder (Quick Action / Service):
   `~/Library/Services/Конвертировать в EPS (Плоттер).workflow`
   Доступно по правому клику на любом изображении (JPEG/PNG/WebP) в Finder -> Быстрые действия.
"""

import plistlib
import stat
import subprocess
import uuid
from pathlib import Path

PROJECT_DIR = Path("/Users/danilakonovalov/.gemini/antigravity/scratch/jpeg2plotter-eps")
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"
MAIN_SCRIPT = PROJECT_DIR / "jpeg2eps.py"

LOCAL_BIN = Path.home() / ".local" / "bin"
CLI_WRAPPER = LOCAL_BIN / "jpeg2eps"

SERVICES_DIR = Path.home() / "Library" / "Services"
WORKFLOW_NAME = "Конвертировать в EPS (Плоттер).workflow"
WORKFLOW_DIR = SERVICES_DIR / WORKFLOW_NAME


def install_cli_wrapper() -> None:
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    wrapper_code = f"""#!/bin/zsh
exec "{VENV_PYTHON}" "{MAIN_SCRIPT}" "$@"
"""
    CLI_WRAPPER.write_text(wrapper_code, encoding="utf-8")
    CLI_WRAPPER.chmod(CLI_WRAPPER.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"✅ Установлена CLI-команда: {CLI_WRAPPER}")


def install_finder_quick_action() -> None:
    contents_dir = WORKFLOW_DIR / "Contents"
    contents_dir.mkdir(parents=True, exist_ok=True)

    shell_command = f'"{VENV_PYTHON}" "{MAIN_SCRIPT}" --notify "$@" >> /tmp/jpeg2eps_finder.log 2>&1'

    info_plist = {
        "NSServices": [
            {
                "NSBackgroundColorName": "background",
                "NSIconName": "NSActionTemplate",
                "NSMenuItem": {
                    "default": "Конвертировать в EPS (Плоттер)",
                },
                "NSMessage": "runWorkflowAsService",
                "NSRequiredContext": {
                    "NSApplicationIdentifier": "com.apple.finder",
                },
                "NSSendFileTypes": [
                    "public.item",
                    "public.image",
                    "public.jpeg",
                    "public.png",
                    "public.folder",
                ],
            }
        ]
    }

    action_uuid = str(uuid.uuid4()).upper()
    input_uuid = str(uuid.uuid4()).upper()
    output_uuid = str(uuid.uuid4()).upper()

    document_wflow = {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [
            {
                "action": {
                    "AMAccepts": {
                        "Container": "List",
                        "Optional": True,
                        "Types": ["com.apple.cocoa.string", "com.apple.cocoa.path"],
                    },
                    "AMActionVersion": "2.0.3",
                    "AMApplication": ["Automator"],
                    "AMParameterProperties": {
                        "COMMAND_STRING": {},
                        "CheckedForUserDefaultShell": {},
                        "inputMethod": {},
                        "shell": {},
                        "source": {},
                    },
                    "AMProvides": {
                        "Container": "List",
                        "Types": ["com.apple.cocoa.string"],
                    },
                    "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
                    "ActionName": "Run Shell Script",
                    "ActionParameters": {
                        "COMMAND_STRING": shell_command,
                        "CheckedForUserDefaultShell": True,
                        "inputMethod": 1,  # 1 = как аргументы ("$@")
                        "shell": "/bin/zsh",
                        "source": "",
                    },
                    "BundleIdentifier": "com.apple.RunShellScript",
                    "CFBundleVersion": "2.0.3",
                    "CanShowSelectedItemsWhenRun": False,
                    "CanShowWhenRun": True,
                    "Category": ["AMCategoryUtilities"],
                    "Class Name": "RunShellScriptAction",
                    "InputUUID": input_uuid,
                    "Keywords": ["Shell", "Script", "Command", "Run"],
                    "OutputUUID": output_uuid,
                    "UUID": action_uuid,
                    "UnlocalizedApplications": ["Automator"],
                    "arguments": {
                        "0": {
                            "default value": 1,
                            "name": "inputMethod",
                            "required": "0",
                            "type": "0",
                            "uuid": "0",
                        },
                        "1": {
                            "default value": "",
                            "name": "source",
                            "required": "0",
                            "type": "0",
                            "uuid": "1",
                        },
                        "2": {
                            "default value": False,
                            "name": "CheckedForUserDefaultShell",
                            "required": "0",
                            "type": "0",
                            "uuid": "2",
                        },
                        "3": {
                            "default value": "",
                            "name": "COMMAND_STRING",
                            "required": "0",
                            "type": "0",
                            "uuid": "3",
                        },
                        "4": {
                            "default value": "/bin/zsh",
                            "name": "shell",
                            "required": "0",
                            "type": "0",
                            "uuid": "4",
                        },
                    },
                    "isViewVisible": 1,
                },
                "isViewVisible": 1,
            }
        ],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleID": "com.apple.finder",
            "applicationBundleIDsByPath": {
                "/System/Library/CoreServices/Finder.app": "com.apple.finder"
            },
            "applicationPath": "/System/Library/CoreServices/Finder.app",
            "applicationPaths": ["/System/Library/CoreServices/Finder.app"],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "processesInput": 0,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }

    with open(contents_dir / "Info.plist", "wb") as f:
        plistlib.dump(info_plist, f)

    with open(contents_dir / "document.wflow", "wb") as f:
        plistlib.dump(document_wflow, f)

    # Обновляем кэш служб macOS (pbs)
    pbs_bin = Path("/System/Library/CoreServices/pbs")
    if pbs_bin.exists():
        subprocess.run([str(pbs_bin), "-flush"], check=False)
        subprocess.run([str(pbs_bin), "-update"], check=False)

    print(f"✅ Установлено Быстрое действие Finder: {WORKFLOW_DIR}")


if __name__ == "__main__":
    install_cli_wrapper()
    install_finder_quick_action()

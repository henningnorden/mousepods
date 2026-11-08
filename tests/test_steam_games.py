# SPDX-License-Identifier: Apache-2.0
"""Steam library discovery (games, Proton prefixes, launch options).

Run:  python -m unittest tests.test_steam_games -v
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import steam_games as sg  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_steam(home, rel=".local/share/Steam", second_lib=None):
    """A fake Steam install: AC (with prefix) in the main library, ETS2 (no prefix) in the second."""
    root = os.path.join(home, rel)
    libs = [root] + ([second_lib] if second_lib else [])
    vdf = '"libraryfolders"\n{\n'
    for i, lib in enumerate(libs):
        vdf += f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{lib}"\n\t\t"apps"\n\t\t{{\n\t\t}}\n\t}}\n'
    vdf += "}\n"
    write(os.path.join(root, "steamapps", "libraryfolders.vdf"), vdf)
    write(os.path.join(root, "steamapps", "appmanifest_244210.acf"),
          '"AppState"\n{\n\t"appid"\t\t"244210"\n\t"name"\t\t"Assetto Corsa"\n\t"installdir"\t\t"assettocorsa"\n}\n')
    pfx = os.path.join(root, "steamapps", "compatdata", "244210", "pfx")
    write(os.path.join(pfx, "user.reg"), "WINE REGISTRY Version 2\n")
    write(os.path.join(root, "steamapps", "compatdata", "244210", "config_info"),
          "GE-Proton9-20\n/home/x/.local/share/Steam/compatibilitytools.d/GE-Proton9-20/files/share/fonts/\n")
    lib2 = second_lib or root
    write(os.path.join(lib2, "steamapps", "appmanifest_227300.acf"),
          '"AppState"\n{\n\t"appid"\t\t"227300"\n\t"name"\t\t"Euro Truck Simulator 2"\n}\n')
    write(os.path.join(root, "userdata", "123", "config", "localconfig.vdf"),
          '"UserLocalConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
          '\t\t\t\t"apps"\n\t\t\t\t{\n\t\t\t\t\t"244210"\n\t\t\t\t\t{\n'
          '\t\t\t\t\t\t"LaunchOptions"\t\t"$HOME/.local/bin/opentrack-launcher %command%"\n'
          '\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n')
    return root


class SteamGamesTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.home)

    def test_parse_vdf_nested(self):
        d = sg.parse_vdf('// comment\n"a"\n{\n\t"b"\t"1"\n\t"c"\n\t{\n\t\t"d"\t"x \\"y\\""\n\t}\n}\n')
        self.assertEqual(d, {"a": {"b": "1", "c": {"d": 'x "y"'}}})

    def test_lists_games_with_names_and_prefix(self):
        make_steam(self.home)
        root = sg.find_steam_roots(self.home)[0]
        games = {g.appid: g for g in sg.list_games(root)}
        self.assertEqual(games["244210"].name, "Assetto Corsa")
        self.assertTrue(games["244210"].prefix.endswith("compatdata/244210/pfx"))
        self.assertEqual(games["227300"].name, "Euro Truck Simulator 2")

    def test_game_without_prefix_has_none(self):
        make_steam(self.home)
        games = {g.appid: g for g in sg.list_games(sg.find_steam_roots(self.home)[0])}
        self.assertIsNone(games["227300"].prefix)
        self.assertIsNone(games["227300"].proton)

    def test_proton_from_config_info(self):
        make_steam(self.home)
        games = {g.appid: g for g in sg.list_games(sg.find_steam_roots(self.home)[0])}
        self.assertTrue(games["244210"].proton.endswith("compatibilitytools.d/GE-Proton9-20"))

    def test_launch_options_detect_opentrack_launcher(self):
        make_steam(self.home)
        games = {g.appid: g for g in sg.list_games(sg.find_steam_roots(self.home)[0])}
        self.assertIn("opentrack-launcher", games["244210"].launch_options)
        self.assertEqual(games["227300"].launch_options, "")

    def test_second_library_and_flatpak_steam(self):
        lib2 = os.path.join(self.home, "mnt", "SteamLibrary")
        make_steam(self.home, rel=".var/app/com.valvesoftware.Steam/.local/share/Steam", second_lib=lib2)
        roots = sg.find_steam_roots(self.home)
        self.assertEqual(len(roots), 1)
        self.assertTrue(roots[0].flatpak)
        games = {g.appid: g for g in sg.list_games(roots[0])}
        self.assertEqual(games["227300"].library, lib2)
        self.assertTrue(all(g.flatpak for g in games.values()))

    def test_non_numeric_appid_manifest_is_skipped(self):
        root = make_steam(self.home)
        write(os.path.join(root, "steamapps", "appmanifest_x.acf"),
              '"AppState"\n{\n\t"appid"\t\t"1\\" onmouseover=\\"x"\n\t"name"\t\t"Evil"\n}\n')
        ids = [g.appid for g in sg.list_games(sg.find_steam_roots(self.home)[0])]
        self.assertEqual(sorted(ids), ["227300", "244210"])

    def test_same_library_under_two_paths_listed_once(self):
        # Bazzite: Steam writes /var/home/..., a distrobox sees /home/... (same directory)
        root = make_steam(self.home)
        alias = os.path.join(self.home, "alias")
        os.symlink(self.home, alias)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        write(vdf, '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
              % os.path.join(alias, ".local/share/Steam"))
        with mock.patch("os.path.realpath", side_effect=lambda p: p):   # no symlink resolution, like /var/home
            ids = [g.appid for g in sg.list_games(sg.SteamRoot(root, False))]
        self.assertEqual(sorted(ids), ["227300", "244210"])

    def test_steam_tools_are_not_games(self):
        root = make_steam(self.home)
        for appid, name in (("1493710", "Proton Experimental"), ("2348590", "Proton 8.0"),
                            ("1628350", "Steam Linux Runtime 3.0 (sniper)"),
                            ("228980", "Steamworks Common Redistributables")):
            write(os.path.join(root, "steamapps", f"appmanifest_{appid}.acf"),
                  '"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n}\n' % (appid, name))
        ids = [g.appid for g in sg.list_games(sg.find_steam_roots(self.home)[0])]
        self.assertEqual(sorted(ids), ["227300", "244210"])

    def test_no_steam_is_empty(self):
        self.assertEqual(sg.find_steam_roots(self.home), [])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the pipeline's pure maths. Run with `head test`, `python -m unittest` or pytest."""
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import cli  # noqa: E402
import glb  # noqa: E402
import headmath  # noqa: E402
import server  # noqa: E402

MARKS = {
    "eyes": [{"at": [437, 448], "half": [34, 40]}, {"at": [587, 448], "half": [34, 40]}],
    "mouth": {"at": [512, 578], "half": [48, 26]},
    "chin": 646,
}


class PlaneMapping(unittest.TestCase):
    def test_centre_of_a_square_picture_is_the_origin(self):
        self.assertEqual(headmath.to_plane((512, 512), 1024, 1024), (0, 0))

    def test_corners_of_a_square_picture_are_at_one(self):
        self.assertEqual(headmath.to_plane((0, 0), 1024, 1024), (-1, 1))
        self.assertEqual(headmath.to_plane((1024, 1024), 1024, 1024), (1, -1))

    def test_a_tall_picture_spans_one_up_and_less_across(self):
        self.assertEqual(headmath.to_plane((0, 0), 500, 1000), (-0.5, 1))

    def test_to_pixel_undoes_to_plane(self):
        x, y = headmath.to_pixel(headmath.to_plane((123, 456), 800, 600), 800, 600)
        self.assertAlmostEqual(x, 123)
        self.assertAlmostEqual(y, 456)


class Smoothstep(unittest.TestCase):
    def test_is_clamped_outside_the_range(self):
        self.assertEqual(headmath.smoothstep(0, 1, -3), 0)
        self.assertEqual(headmath.smoothstep(0, 1, 9), 1)

    def test_is_half_way_in_the_middle(self):
        self.assertAlmostEqual(headmath.smoothstep(2, 4, 3), 0.5)


class Neck(unittest.TestCase):
    def test_finds_the_narrow_row_between_head_and_shoulders(self):
        widths = [0] * 5 + [40, 60, 70, 70, 60, 40, 20, 18, 22, 60, 90, 100]
        self.assertEqual(headmath.find_neck(widths), 12)

    def test_a_head_with_no_body_has_no_neck(self):
        self.assertIsNone(headmath.find_neck([0, 20, 40, 60, 70, 70, 60, 40, 20, 10]))

    def test_an_empty_outline_has_no_neck(self):
        self.assertIsNone(headmath.find_neck([0] * 30))


class Marks(unittest.TestCase):
    def test_good_marks_have_no_errors(self):
        self.assertEqual(headmath.validate_marks(MARKS, 1024, 1024), [])

    def test_guessed_marks_are_valid(self):
        guess = headmath.guess_marks((276, 176, 748, 668))
        self.assertEqual(headmath.validate_marks(guess, 1024, 1024), [])
        self.assertLess(guess["eyes"][0]["at"][0], guess["eyes"][1]["at"][0])

    def test_reports_a_missing_mouth(self):
        errors = headmath.validate_marks({"eyes": MARKS["eyes"]}, 1024, 1024)
        self.assertTrue(any("mouth" in error for error in errors))

    def test_reports_a_mouth_above_the_eyes(self):
        marks = {**MARKS, "mouth": {"at": [512, 300], "half": [48, 26]}}
        self.assertIn("the mouth must be below both eyes", headmath.validate_marks(marks, 1024, 1024))

    def test_reports_eyes_in_the_wrong_order(self):
        marks = {**MARKS, "eyes": list(reversed(MARKS["eyes"]))}
        self.assertIn("eyes[0] must be the one on the left of the picture", headmath.validate_marks(marks, 1024, 1024))

    def test_reports_a_point_outside_the_picture(self):
        marks = {**MARKS, "mouth": {"at": [5000, 578], "half": [48, 26]}}
        self.assertTrue(any("outside" in error for error in headmath.validate_marks(marks, 1024, 1024)))

    def test_reports_a_chin_above_the_mouth(self):
        self.assertTrue(headmath.validate_marks({**MARKS, "chin": 500}, 1024, 1024))

    def test_rejects_things_that_are_not_marks(self):
        self.assertEqual(headmath.validate_marks([1, 2], 10, 10), ["marks must be a JSON object"])

    def test_complete_marks_fills_in_the_chin(self):
        without = {key: value for key, value in MARKS.items() if key != "chin"}
        self.assertAlmostEqual(headmath.complete_marks(without)["chin"], 578 + 0.62 * (578 - 448))
        self.assertEqual(headmath.complete_marks(MARKS)["chin"], 646)


class Config(unittest.TestCase):
    def test_the_defaults_are_valid(self):
        self.assertEqual(headmath.validate_config({}), [])
        self.assertEqual(headmath.validate_config(dict(headmath.DEFAULT_CONFIG)), [])

    def test_reports_unknown_settings(self):
        self.assertEqual(headmath.validate_config({"colour": "red"}), ['unknown setting "colour"'])

    def test_reports_bad_values(self):
        errors = headmath.validate_config({"faces": 10, "texture": 1000, "front": "up", "up": "z", "budgetKB": "big", "jawDrop": 3})
        self.assertEqual(len(errors), 6)

    def test_every_way_up_and_front_has_a_turn(self):
        for up in headmath.UP_TURNS:
            self.assertEqual(headmath.validate_config({"up": up}), [])
        for front in headmath.FRONT_TURNS:
            self.assertEqual(headmath.validate_config({"front": front}), [])

    def test_true_is_not_a_number(self):
        self.assertTrue(headmath.validate_config({"faces": True}))

    def test_merged_config_keeps_defaults_for_what_is_not_given(self):
        merged = headmath.merged_config({"faces": 9000})
        self.assertEqual(merged["faces"], 9000)
        self.assertEqual(merged["texture"], headmath.DEFAULT_CONFIG["texture"])


class Budget(unittest.TestCase):
    SETTINGS = {"faces": 24000, "texture": 2048, "jpegQuality": 88, "budgetKB": 1000}

    def test_decimate_ratio_never_adds_faces(self):
        self.assertEqual(headmath.decimate_ratio(1000, 5000), 1.0)
        self.assertEqual(headmath.decimate_ratio(60000, 24000), 0.4)
        self.assertEqual(headmath.decimate_ratio(0, 24000), 1.0)

    def test_a_file_that_fits_changes_nothing(self):
        self.assertEqual(headmath.next_budget_step(900 * 1024, self.SETTINGS), self.SETTINGS)

    def test_quality_goes_first_then_texture_then_faces(self):
        over = 2000 * 1024
        first = headmath.next_budget_step(over, self.SETTINGS)
        self.assertEqual(first["jpegQuality"], 80)
        lowest_quality = {**self.SETTINGS, "jpegQuality": headmath.MIN_JPEG_QUALITY}
        self.assertEqual(headmath.next_budget_step(over, lowest_quality)["texture"], 1024)
        smallest_texture = {**lowest_quality, "texture": headmath.MIN_TEXTURE}
        self.assertEqual(headmath.next_budget_step(over, smallest_texture)["faces"], 16800)

    def test_gives_up_when_nothing_is_left(self):
        least = {"faces": headmath.MIN_FACES, "texture": headmath.MIN_TEXTURE, "jpegQuality": headmath.MIN_JPEG_QUALITY, "budgetKB": 10}
        self.assertIsNone(headmath.next_budget_step(999999, least))

    def test_the_steps_always_end(self):
        settings, steps = dict(self.SETTINGS), 0
        while settings is not None and steps < 100:
            settings, steps = headmath.next_budget_step(10 ** 9, settings), steps + 1
        self.assertIsNone(settings)


class Jaw(unittest.TestCase):
    def setUp(self):
        self.jaw = headmath.jaw_frame(MARKS, 1024, 1024, mouth_depth=0.3)

    def test_frame_is_in_plane_units(self):
        self.assertAlmostEqual(self.jaw["mouthY"], (512 - 578) / 512)
        self.assertAlmostEqual(self.jaw["chinY"], (512 - 646) / 512)
        self.assertAlmostEqual(self.jaw["drop"], self.jaw["unit"] * headmath.JAW_DROP_SHARE)

    def test_a_given_drop_is_kept(self):
        self.assertEqual(headmath.jaw_frame(MARKS, 1024, 1024, 0.3, drop=0.05)["drop"], 0.05)

    def test_nothing_moves_above_the_mouth(self):
        self.assertEqual(headmath.jaw_pull(0, self.jaw["mouthY"] + 0.05, 0.3, self.jaw), 0)

    def test_the_chin_moves_fully(self):
        self.assertAlmostEqual(headmath.jaw_pull(0, self.jaw["chinY"] + 0.02, 0.3, self.jaw), 1, places=2)

    def test_the_cheeks_do_not_move(self):
        self.assertEqual(headmath.jaw_pull(self.jaw["reach"] * 1.1, self.jaw["chinY"] + 0.02, 0.3, self.jaw), 0)

    def test_the_neck_behind_the_chin_does_not_move(self):
        self.assertEqual(headmath.jaw_pull(0, self.jaw["chinY"] + 0.02, 0.3 - 1.5 * self.jaw["unit"], self.jaw), 0)

    def test_the_throat_below_the_chin_does_not_move(self):
        self.assertEqual(headmath.jaw_pull(0, self.jaw["chinY"] - self.jaw["unit"], 0.3, self.jaw), 0)

    def test_pull_stays_between_zero_and_one(self):
        for step in range(-20, 21):
            pull = headmath.jaw_pull(step * 0.01, self.jaw["mouthY"] - abs(step) * 0.01, 0.3, self.jaw)
            self.assertTrue(0 <= pull <= 1)

    def test_mouth_drop_is_a_share_of_the_jaw(self):
        self.assertAlmostEqual(headmath.mouth_drop(0.04, 1), 0.04 * headmath.JAW_SHARE)
        self.assertEqual(headmath.mouth_drop(0.04, 0), 0)


def surface():
    grid = {"cols": 2, "rows": 2, "span": 1.8, "depth": [0.1, 0.1, 0.1, 0.1]}
    feature = {"at": [0, 0], "half": [0.1, 0.1], "grid": grid}
    return {"version": 1, "eyes": [dict(feature), dict(feature)], "mouth": dict(feature), "jaw": {"drop": 0.02, "share": 0.45},
            "colors": {"lid": "#f6c39c", "lash": "#1d2433", "mouth": "#3a1215", "tongue": "#c9645c", "teeth": "#f4efe8"}}


class Surface(unittest.TestCase):
    def test_a_complete_surface_has_no_errors(self):
        self.assertEqual(headmath.validate_surface(surface()), [])

    def test_reports_each_missing_part(self):
        for key in ("eyes", "mouth", "jaw", "colors", "version"):
            broken = surface()
            del broken[key]
            self.assertTrue(headmath.validate_surface(broken), key)

    def test_reports_a_grid_of_the_wrong_size(self):
        broken = surface()
        broken["mouth"] = {**broken["mouth"], "grid": {"cols": 3, "rows": 3, "span": 1.8, "depth": [0.1] * 4}}
        self.assertEqual(headmath.validate_surface(broken), ["mouth.grid.depth must have cols x rows numbers"])

    def test_reports_a_bad_colour(self):
        broken = surface()
        broken["colors"] = {**broken["colors"], "lid": "skin"}
        self.assertEqual(headmath.validate_surface(broken), ["colors.lid must be a #rrggbb colour"])

    def test_to_hex(self):
        self.assertEqual(headmath.to_hex((1, 0, 0.5)), "#ff0080")
        self.assertEqual(headmath.to_hex((2, -1, 0)), "#ff0000")
        self.assertTrue(headmath.is_hex_colour("#A0b1C2"))
        self.assertFalse(headmath.is_hex_colour("#12345"))


class CommandLine(unittest.TestCase):
    def test_an_axis_starting_with_a_dash_is_read_as_a_value(self):
        self.assertEqual(cli.joined_axis_options(["build", "p", "--front", "-y", "--up", "+y", "--faces", "9000"]),
                         ["build", "p", "--front=-y", "--up=+y", "--faces", "9000"])
        args = cli.parser().parse_args(cli.joined_axis_options(["build", "p", "--up", "-y"]))
        self.assertEqual(args.up, "-y")

    def test_reads_the_size_of_a_png_from_its_header(self):
        portrait = Path(__file__).resolve().parent.parent / "examples" / "juno" / "portrait.png"
        self.assertEqual(cli.picture_size(portrait), (1024, 1024))


def make_glb(description: dict, pad: bytes = b"") -> bytes:
    body = json.dumps(description).encode()
    body += b" " * (-len(body) % 4)
    return struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(body) + len(pad)) + struct.pack("<II", len(body), 0x4E4F534A) + body + pad


class Glb(unittest.TestCase):
    DESCRIPTION = {
        "asset": {"version": "2.0", "generator": "test"},
        "accessors": [{"count": 300}, {"count": 900}, {"count": 300}],
        "meshes": [{"extras": {"targetNames": ["jaw"]}, "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "targets": [{"POSITION": 2}]}]}],
        "images": [{"mimeType": "image/jpeg"}],
    }

    def test_reads_what_verify_needs(self):
        info = glb.summary(glb.parse_glb(make_glb(self.DESCRIPTION)))
        self.assertEqual((info["triangles"], info["points"], info["targets"], info["images"]), (300, 300, ["jaw"], ["image/jpeg"]))

    def test_a_target_with_no_name_is_numbered(self):
        description = json.loads(json.dumps(self.DESCRIPTION))
        del description["meshes"][0]["extras"]
        self.assertEqual(glb.summary(description)["targets"], ["target0"])

    def test_rejects_a_file_that_is_not_a_glb(self):
        with self.assertRaises(glb.GlbError):
            glb.parse_glb(b"not a model at all, just some text")

    def test_rejects_a_cut_off_file(self):
        with self.assertRaises(glb.GlbError):
            glb.parse_glb(make_glb(self.DESCRIPTION)[:-4])


class Server(unittest.TestCase):
    def test_paths_cannot_leave_their_folder(self):
        root = Path(__file__).resolve().parent
        self.assertIsNone(server.resolve_under(root, "../LICENSE"))
        self.assertIsNone(server.resolve_under(root, "a/../../LICENSE"))
        self.assertEqual(server.resolve_under(root, "/test_headmath.py"), root / "test_headmath.py")

    def test_hidden_files_are_not_served(self):
        skill = Path(__file__).resolve().parent.parent
        routes = server.Routes(skill, None, None)
        self.assertEqual(routes.locate("/.gitignore")[0], "missing")
        self.assertEqual(routes.locate("/LICENSE")[0], "file")
        self.assertEqual(routes.locate("/project/anything")[0], "missing")

    def test_three_is_redirected_when_not_installed(self):
        kind, where = server.Routes(Path("."), None, None).locate("/three/build/three.module.js")
        self.assertEqual(kind, "redirect")
        self.assertTrue(where.endswith("/build/three.module.js"))

    def test_only_png_data_urls_are_accepted(self):
        with self.assertRaises(ValueError):
            server.png_from_data_url(b"data:text/html;base64,AAAA")
        with self.assertRaises(ValueError):
            server.png_from_data_url(b"data:image/png;base64,AAAA")


if __name__ == "__main__":
    unittest.main()

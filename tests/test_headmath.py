"""Unit tests for the pipeline's pure maths. Run with `head test`, `python -m unittest` or pytest."""
import contextlib
import http.client
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import cli  # noqa: E402
import generate_space  # noqa: E402
import picture  # noqa: E402
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


def smile(width: int = 30, centre: int = 20, sag: int = 6) -> list[list[int]]:
    """Columns of a drawn smile two pixels thick, lowest in the middle."""
    columns = []
    for column in range(width):
        row = centre + round(sag * (1 - ((column - width / 2) / (width / 2)) ** 2))
        columns.append([row, row + 1])
    return columns


class LipLine(unittest.TestCase):
    def test_follows_a_drawn_smile_end_to_end(self):
        first, last, rows = headmath.lip_line(smile(), 23, 5)
        self.assertEqual((first, last), (0, 29))
        self.assertEqual(len(rows), 5)
        self.assertGreater(rows[2], rows[0])
        self.assertAlmostEqual(rows[0], rows[-1], delta=1)

    def test_ignores_other_line_work_further_away(self):
        columns = smile()
        columns[10] = columns[10] + [60, 61]   # the crease under the lip, far below
        columns[3] = [0, 1] + columns[3]       # the nose, far above
        _, _, rows = headmath.lip_line(columns, 23, 9)
        self.assertTrue(all(18 <= row <= 28 for row in rows))

    def test_steps_over_a_small_break_in_the_line(self):
        columns = smile()
        for column in (12, 13):
            columns[column] = []
        first, last, _ = headmath.lip_line(columns, 23, 5)
        self.assertEqual((first, last), (0, 29))

    def test_stops_where_the_line_jumps_away(self):
        columns = smile(sag=0)
        for column in range(22, 30):
            columns[column] = [40, 41]
        self.assertEqual(headmath.lip_line(columns, 20, 5)[1], 21)

    def test_no_line_work_means_no_line(self):
        self.assertIsNone(headmath.lip_line([[] for _ in range(30)], 20, 5))
        self.assertIsNone(headmath.lip_line(smile(width=4), 20, 5))
        self.assertIsNone(headmath.lip_line([], 20, 5))


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

    def test_a_lip_line_is_optional_but_must_be_complete(self):
        with_line = surface()
        with_line["mouth"] = {**with_line["mouth"], "line": {"x": [-0.1, 0.1], "y": [0, -0.01, 0]}}
        self.assertEqual(headmath.validate_surface(with_line), [])
        with_line["mouth"]["line"] = {"x": [-0.1, 0.1]}
        self.assertEqual(len(headmath.validate_surface(with_line)), 1)

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

    def test_hidden_files_are_not_served_on_any_route(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for place in ("skill", "project", "three"):
                (root / place / ".secret").mkdir(parents=True)
                (root / place / ".env").write_text("x")
                (root / place / ".secret" / "key").write_text("x")
                (root / place / "open.txt").write_text("x")
            routes = server.Routes(root / "skill", root / "project", root / "three")
            for prefix in ("/", "/project/", "/three/"):
                self.assertEqual(routes.locate(prefix + "open.txt")[0], "file", prefix)
                self.assertEqual(routes.locate(prefix + ".env")[0], "missing", prefix)
                self.assertEqual(routes.locate(prefix + ".secret/key")[0], "missing", prefix)
                self.assertEqual(routes.locate(prefix + "%2Eenv")[0], "missing", prefix)

    def test_nothing_is_sent_elsewhere_when_three_is_not_installed(self):
        self.assertEqual(server.Routes(Path("."), None, None).locate("/three/build/three.module.js"), ("missing", None))

    def test_only_this_machine_on_this_port_is_a_good_host(self):
        for host in ("127.0.0.1:8437", "localhost:8437", "[::1]:8437", "LOCALHOST:8437"):
            self.assertTrue(server.host_allowed(host, 8437), host)
        for host in ("evil.example:8437", "127.0.0.1:9999", "127.0.0.1", "localhost.evil.example:8437", None, ""):
            self.assertFalse(server.host_allowed(host, 8437), host)

    def test_only_png_data_urls_are_accepted(self):
        with self.assertRaises(ValueError):
            server.png_from_data_url("data:text/html;base64,AAAA")
        with self.assertRaises(ValueError):
            server.png_from_data_url("data:image/png;base64,AAAA")
        with self.assertRaises(ValueError):
            server.png_from_data_url(None)


class RunningServer(unittest.TestCase):
    """The real server, on a free port, answering real requests."""

    def setUp(self):
        self.saved = []
        skill = Path(__file__).resolve().parent.parent
        self.routes = server.Routes(skill, None, None, lambda name, payload: (self.saved.append((name, payload)) or (200, "saved")))
        self.web, address = server.start(self.routes)
        self.port = self.routes.port
        self.addCleanup(server.stop, self.web)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest(method, path, skip_host=True)
            for name, value in {"Host": f"127.0.0.1:{self.port}", **(headers or {})}.items():
                if value is not None:
                    connection.putheader(name, value)
            data = body.encode() if isinstance(body, str) else body
            connection.putheader("Content-Length", str(len(data or b"")))
            connection.endheaders(data)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def save(self, **headers):
        good = {"Content-Type": "application/json", server.TOKEN_HEADER: self.routes.token}
        return self.request("POST", "/save/marks", '{"a": 1}', {**good, **headers})[0]

    def test_serves_files_to_a_local_host(self):
        self.assertEqual(self.request("GET", "/LICENSE")[0], 200)
        self.assertEqual(self.request("GET", "/LICENSE", headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_refuses_another_host_name(self):
        self.assertEqual(self.request("GET", "/LICENSE", headers={"Host": f"attacker.example:{self.port}"})[0], 403)
        self.assertEqual(self.save(Host=f"attacker.example:{self.port}"), 403)

    def test_a_good_save_goes_through(self):
        self.assertEqual(self.save(), 200)
        self.assertEqual(self.saved, [("marks", {"a": 1})])

    def test_refuses_a_save_without_the_token(self):
        self.assertEqual(self.save(**{server.TOKEN_HEADER: None}), 403)
        self.assertEqual(self.save(**{server.TOKEN_HEADER: "guess"}), 403)
        self.assertEqual(self.saved, [])

    def test_refuses_a_save_that_is_not_json(self):
        self.assertEqual(self.save(**{"Content-Type": "text/plain"}), 415)
        self.assertEqual(self.save(**{"Content-Type": "application/x-www-form-urlencoded"}), 415)
        self.assertEqual(self.request("POST", "/save/marks", "not json",
                                      {"Content-Type": "application/json", server.TOKEN_HEADER: self.routes.token})[0], 400)
        self.assertEqual(self.saved, [])

    def test_each_run_has_its_own_token(self):
        self.assertNotEqual(server.Routes(Path("."), None, None).token, server.Routes(Path("."), None, None).token)


def tiny_jpeg(orientation=None):
    """A JPEG header with a size and, optionally, an EXIF orientation: enough for the header readers."""
    parts = [b"\xff\xd8"]
    if orientation is not None:
        tiff = b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1) + struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", orientation, 0) + b"\x00\x00\x00\x00"
        payload = b"Exif\x00\x00" + tiff
        parts.append(b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload)
    frame = b"\x08" + struct.pack(">HH", 30, 40) + b"\x03" + b"\x00" * 9
    parts.append(b"\xff\xc0" + struct.pack(">H", len(frame) + 2) + frame + b"\xff\xd9")
    return b"".join(parts)


def tiny_png(orientation=None):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + b"\x00\x00\x00\x00"
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", 50, 60, 8, 6, 0, 0, 0))
    if orientation is not None:
        tiff = b"II\x2a\x00\x08\x00\x00\x00" + struct.pack("<H", 1) + struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<HH", orientation, 0)
        body += chunk(b"eXIf", tiff)
    return b"\x89PNG\r\n\x1a\n" + body + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


class Pictures(unittest.TestCase):
    def test_reads_sizes(self):
        self.assertEqual(picture.size_of(tiny_jpeg()), (40, 30))
        self.assertEqual(picture.size_of(tiny_jpeg(6)), (40, 30))
        self.assertEqual(picture.size_of(tiny_png()), (50, 60))
        with self.assertRaises(picture.PictureError):
            picture.size_of(b"GIF89a")

    def test_reads_exif_orientation_in_jpeg_and_png(self):
        self.assertEqual(picture.orientation_of(tiny_jpeg()), 1)
        self.assertEqual(picture.orientation_of(tiny_jpeg(6)), 6)
        self.assertEqual(picture.orientation_of(tiny_png()), 1)
        self.assertEqual(picture.orientation_of(tiny_png(8)), 8)


class Projects(unittest.TestCase):
    """`head new` and `head shape --mesh`: nothing of the user's is lost, whatever they point at."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.portrait = Path(__file__).resolve().parent.parent / "examples" / "juno" / "portrait.png"

    def run_cli(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            try:
                code = cli.main([str(argument) for argument in arguments])
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 1
                printed.write(str(stop.code))
        return code, printed.getvalue()

    def test_new_with_the_projects_own_portrait_keeps_it(self):
        project = self.folder / "p"
        project.mkdir()
        own = project / "portrait.png"
        shutil.copy(self.portrait, own)
        before = own.read_bytes()
        self.assertEqual(self.run_cli("new", project, own)[0], 0)
        self.assertEqual(own.read_bytes(), before)

    def test_new_with_a_bad_picture_keeps_the_old_one(self):
        project = self.folder / "p"
        self.assertEqual(self.run_cli("new", project, self.portrait)[0], 0)
        (self.folder / "broken.jpg").write_bytes(b"not a picture")
        code, _ = self.run_cli("new", project, self.folder / "broken.jpg")
        self.assertNotEqual(code, 0)
        self.assertTrue((project / "portrait.png").exists())
        self.assertFalse((project / "portrait.jpg").exists())

    def test_new_replaces_a_png_with_a_jpeg_only_after_copying_it(self):
        project = self.folder / "p"
        self.run_cli("new", project, self.portrait)
        jpeg = self.folder / "upright.jpg"
        jpeg.write_bytes(tiny_jpeg())
        self.assertEqual(self.run_cli("new", project, jpeg)[0], 0)
        self.assertTrue((project / "portrait.jpg").exists())
        self.assertFalse((project / "portrait.png").exists())

    def test_new_refuses_a_picture_with_an_orientation_tag(self):
        turned = self.folder / "turned.jpg"
        turned.write_bytes(tiny_jpeg(6))
        code, printed = self.run_cli("new", self.folder / "p", turned)
        self.assertNotEqual(code, 0)
        self.assertIn("orientation", printed)

    def test_shape_with_the_projects_own_mesh_keeps_it(self):
        project = self.folder / "p"
        self.run_cli("new", project, self.portrait)
        raw = project / "work" / "raw.glb"
        raw.parent.mkdir()
        raw.write_bytes(b"my mesh")
        self.assertEqual(self.run_cli("shape", project, "--mesh", raw)[0], 0)
        self.assertEqual(raw.read_bytes(), b"my mesh")

    def test_shape_with_a_bad_mesh_keeps_the_old_one(self):
        project = self.folder / "p"
        self.run_cli("new", project, self.portrait)
        raw = project / "work" / "raw.obj"
        raw.parent.mkdir()
        raw.write_text("v 0 0 0")
        code, _ = self.run_cli("shape", project, "--mesh", self.folder / "missing.glb")
        self.assertNotEqual(code, 0)
        self.assertEqual(raw.read_text(), "v 0 0 0")

    def test_shape_replaces_the_old_mesh_after_copying_the_new_one(self):
        project = self.folder / "p"
        self.run_cli("new", project, self.portrait)
        (project / "work").mkdir()
        (project / "work" / "raw.obj").write_text("old")
        new_mesh = self.folder / "new.glb"
        new_mesh.write_bytes(b"new")
        self.assertEqual(self.run_cli("shape", project, "--mesh", new_mesh)[0], 0)
        self.assertEqual((project / "work" / "raw.glb").read_bytes(), b"new")
        self.assertFalse((project / "work" / "raw.obj").exists())

    def test_the_public_generator_needs_an_explicit_yes(self):
        project = self.folder / "p"
        self.run_cli("new", project, self.portrait)
        with unittest.mock.patch.object(cli.subprocess, "run") as run, \
                unittest.mock.patch.object(cli.sys.stdin, "isatty", return_value=False):
            code, printed = self.run_cli("shape", project, "--source", "space")
        self.assertEqual(code, 1)
        run.assert_not_called()
        for words in ("Hugging Face", "tencent/Hunyuan3D-2", "European Union", "United Kingdom", "South Korea", "--yes-upload"):
            self.assertIn(words, printed)

    def test_check_does_not_go_online_unless_asked(self):
        with unittest.mock.patch.object(cli, "space_status") as status:
            self.run_cli("check")
            status.assert_not_called()
            status.return_value = (True, "stage RUNNING")
            self.run_cli("check", "--online")
            status.assert_called_once()

    def test_the_example_is_a_valid_project(self):
        example = Path(__file__).resolve().parent.parent / "examples" / "juno"
        checks = cli.verify_project(cli.Project(str(example)), 1500)
        self.assertTrue(all(passed for passed, _ in checks), checks)
        self.assertNotIn("estimated", json.loads((example / "marks.json").read_text()))


class Generator(unittest.TestCase):
    def test_the_upload_is_anonymous(self):
        self.assertIs(generate_space.anonymous_client_options()["token"], False)


if __name__ == "__main__":
    unittest.main()

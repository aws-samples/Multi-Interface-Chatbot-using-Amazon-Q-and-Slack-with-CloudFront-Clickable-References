import os
import json
from pathlib import Path
import pypandoc
from aws_lambda_powertools import Logger

logger = Logger()

BASE_URL = "https://spack.readthedocs.io/en/latest/"


def save_file(file_path, data):
    a = open(file_path, 'w', encoding="utf-8")
    a.write(data)
    a.close()


def save_json(output_path, data):
    with open(output_path, 'w', encoding="utf-8") as f:
        json.dump(data, f, indent=6)


def get_sections(s):
    for sec in s.split('\n# '):
        yield sec if sec.startswith('# ') else '# ' + sec


def split_markdown_by_headers(markdown_content):
    chunks = []
    current_chunk = []
    in_code_block = False

    for line in markdown_content.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block

        if in_code_block:
            current_chunk.append(line)
        elif (
                line.strip().startswith("# ") or
                line.strip().startswith("## ") or
                line.strip().startswith("### ")
        ) and not line.strip().startswith("######"):
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
            current_chunk.append(line)
        else:
            current_chunk.append(line)

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def url_title(title):
    # below order is important
    return title \
        .replace("#", "") \
        .strip() \
        .replace("`", "") \
        .replace(" ", "-") \
        .replace("/", "") \
        .replace("(", "") \
        .replace(")", "") \
        .lower()


def clean_title(title):
    # below order is important
    return title \
        .replace("#", "") \
        .strip() \
        .replace("`", "")


def get_section_title(s):
    if s.startswith("---\ntitle:"):
        _s = s.replace("---\ntitle:", "")
        title_end = _s.find("---")
        return clean_title(_s[:title_end]), url_title(_s[:title_end])

    title = s.split('\n')[0]

    remove_start = title.find("{")
    remove_end = title.find("}")
    if remove_start != -1 and remove_end != -1:
        title = title[:remove_start] + title[remove_end + 1:]

    return clean_title(title), url_title(title)


def create_metadata(header, title, base_url):

    return {
        "Attributes": {
            "_source_uri": f"{base_url}#{header}",
            "data_source": "documentation"
        },
        "Title": f"{title}",
        "ContentType": "MD",
    }


def convert_to_md(input_path, output_path):
    logger.info("convert_to_md")
    logger.info(f"input_path: {input_path}")
    logger.info(f"output_path: {output_path}")
    counter = 0
    for subdir, dirs, files in os.walk(input_path):
        for file in files:
            logger.info(f"subdir={subdir}, dirs={dirs}, files={files}")

            file_path = os.path.join(subdir, file)
            logger.info(f"file_path={file_path}")

            _subdir = subdir.replace(input_path, "")
            if _subdir.startswith("/"):
                _subdir = _subdir[1:]
            logger.info(f"_subdir={_subdir}")

            output_file = os.path.join(_subdir, file)
            logger.info(f"output_file={output_file} [remove input path]")

            output_file = os.path.join(output_path, output_file)
            logger.info(f"output_file={output_file} [new dir]")

            output_file = output_file.replace(".rst", ".md")
            logger.info(f"output_file={output_file} [rename  rst to md]")

            logger.info(f"creating path={Path(output_file).parent}")
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            try:
                pypandoc.convert_file(
                    file_path, 'md',
                    outputfile=output_file,
                    format='rst'
                )
            except Exception as e:
                logger.error(f"Error: {e}")
            counter += 1
    logger.info(f"Number of files converted to md: {counter}")


def split_and_create_metadata(input_path, split_path, metadata_path):
    logger.info("split_and_create_metadata")
    logger.info(f"split_path: {split_path}")
    logger.info(f"metadata_path: {metadata_path}")

    counter = 0
    import glob
    read_path = os.path.join(input_path)
    logger.info(f"Scanning for files in:{input_path + '/**/*.md'}")
    paths_list = glob.glob(input_path + "/**/*.md", recursive=True)
    file_names = [path for path in paths_list if os.path.isfile(path)]
    logger.info(f"file_names: {file_names}")
    logger.info(f"num file_names: {len(file_names)}")

    for file_name in file_names:
        logger.info(f"file_name: {file_name}")
        file_name = file_name.replace(input_path, "")
        if file_name.startswith("/"):
            file_name = file_name[1:]

        with open(os.path.join(read_path, file_name), "r", encoding="utf-8") as f:
            file_content = f.read()

        for i, _sec in enumerate(split_markdown_by_headers(file_content)):
            _clean_section_title, _url_section_title = get_section_title(_sec)
            logger.info(f"Title: {_clean_section_title}")

            _file_name = f"{file_name}#{_url_section_title}.txt"
            logger.info(f"_file_name: {_file_name}")

            _file_path = os.path.join(split_path, _file_name)
            logger.info(f"Saving file to path: {_file_path}")

            Path(_file_path).parent.mkdir(parents=True, exist_ok=True)
            save_file(_file_path, data=_sec)

            metadata_url = BASE_URL

            _metadata = create_metadata(
                header=_url_section_title,
                title=_clean_section_title,
                base_url=metadata_url + str(file_name).replace(".md", ".html")
            )
            _output_path = os.path.join(metadata_path, _file_name + ".metadata.json")
            logger.info(f"Saving metadata to path: {_output_path}")
            Path(_output_path).parent.mkdir(parents=True, exist_ok=True)
            save_json(
                output_path=_output_path,
                data=_metadata
            )
            counter += 1

    logger.info(f"Number of files processed: {counter}")

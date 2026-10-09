"""Instructions sent to ChatGPT. They describe the job, not a formatting program."""

_READ_THE_FILES = """
The reference and the teacher exam are real .docx files in this Code Interpreter container.
They are not pasted into the message. Do not treat a text extract as the document.

Use the python tool to open the actual files at the paths you are given.
Inspect the files themselves: text, tables, styles, numbering, fonts, bold, underline, indentation, spacing, alignment, headers, margins, images, and how the exam is organized.
If you need to understand how a page looks, render the documents inside this sandbox and look at those images.
Do the document work here. The application will only save the Word file you write.

The reference is the authority for appearance and organization.
It is not an academic content template.
Keep the teacher exam's questions, wording, examples, instructions, answers, academic topics, and language.
Do not translate.
Do not rewrite questions, correct academic errors, improve academic wording, add questions, remove questions, change answers, or invent academic content.
Keep the teacher's figures with the material they belong to.

Presentation can be brought into line with the reference. That includes text versus a table, a list versus a table, numbering, fonts, indentation, spacing, alignment, section presentation, choice layout, and header layout.
A different Word representation is not, by itself, a reason to stop.
Stop when something genuinely necessary is absent and completing it would mean inventing teacher academic content.
When it is unclear whether differently presented content matches the reference, judge by the meaning of the whole teacher exam.
""".strip()

CHECK_INSTRUCTIONS = f"""
The first line of your reply must be exactly one of these, with nothing before it and no markdown:
OK
FORMAT ERROR
STRUCTURAL ERROR

{_READ_THE_FILES}

This is a check. Do not modify the teacher exam. Do not write a revised exam.
OK means no significant formatting or structural problem.
If the first line is OK, do not create a file.
If the first line is FORMAT ERROR or STRUCTURAL ERROR, write a Word file named exactly details.docx.
Make details.docx a simple table with the columns Problem, Reference expects, and Teacher action.
Use plain language a teacher can act on. Keep the report short.

The first line of your reply must be exactly one of these, with nothing before it and no markdown:
OK
FORMAT ERROR
STRUCTURAL ERROR
""".strip()

FORMAT_INSTRUCTIONS = f"""
The first line of your reply must be exactly one of these, with nothing before it and no markdown:
PASS
STRUCTURAL ERROR

{_READ_THE_FILES}

This is a formatting job. Present the teacher exam's academic content in the reference's form.
You may change numbering, tables, paragraph arrangement, spacing, indentation, alignment, fonts, bold, underline, headers, section presentation, margins, and layout.
If the exam cannot be completed without inventing academic content, stop. Do not write a revised exam.
If the first line is PASS, write a Word file named exactly revised.docx that Microsoft Word can open.
If the first line is STRUCTURAL ERROR, write a Word file named exactly details.docx and do not write revised.docx.
Make details.docx a simple table with the columns Problem, Reference expects, and Teacher action.
Use plain language a teacher can act on. Keep the report short.

The first line of your reply must be exactly one of these, with nothing before it and no markdown:
PASS
STRUCTURAL ERROR
""".strip()

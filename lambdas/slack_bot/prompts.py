def get_question_prompt(user_prompt, passage_str):
    return f"""
<question>{user_prompt}</question>
<answer_source>{passage_str}</answer_source>
"""


def get_source_prompt(user_prompt, passage_w_links_str):
    return f"""
<instructions>
You are provided with an answer to a question. Your job is to find the supporting source links using the answer source.
Your responses should contain unique links.
Use provided template for your response. 
</instructions>

<template>
*Sources:*
 [1] www.sample1.com
 [2] www.sample2.com
 [3] www.sample3.com
</template>

<question>
{user_prompt}
</question>
<answer_source>
{passage_w_links_str}
</answer_source>
"""

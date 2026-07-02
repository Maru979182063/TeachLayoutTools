"""聚焦题目解析启发式规则的测试。"""
from app.parser import parse_questions


def test_parse_fullwidth_question_numbers_and_sections():
    """验证parse fullwidth 题目 numbers and sections。"""
    text = """期末真题必刷常考 60 题
一．一元二次方程的定义（共1小题）
1．若关于 x 的方程是二次方程，则 a 的取值范围是         ．
二．一元二次方程的一般形式（共1小题）
2．一元二次方程 2x2+x-5=0 的二次项系数分别是（  ）
A．2，1，5 B．2，1，-5
"""
    questions = parse_questions(text)
    assert len(questions) == 2
    assert questions[0]["number"] == 1
    assert questions[0]["section"].startswith("一．一元二次方程")
    assert questions[1]["number"] == 2

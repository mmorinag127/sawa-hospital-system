from copy import deepcopy
from experiments.agent_assistant.lunch_review import build_lunch_review


def test_saved_grouped_facts_show_actual_food_names_places_and_missing_values():
    review={'evidence':[{'operation':'lunch_get_process','data':{'menu':{'name':'スープ'},'document':{
        'ingredients':[{'id':'c','name':'にんじん'},{'id':'p','name':'じゃがいも'}],
        'route_config':{'template_id':'rice'},'steps':[{'operation':'洗浄','daily_work':{'mode':'separate','placements':[
            {'ingredient_ids':['c'],'equipment':{'id':'sink-a'}},{'ingredient_ids':['p'],'equipment':{'id':'sink-b'}}]},
            'execution':{'start_time':'08:00','end_time':'08:30','assignees':['担当A']}}]}}}]}
    questions={'sources':{'menus':[{'options':{'templates':[{'template_id':'rice','equipment':[{'id':'sink-a','name':'シンクA'},{'id':'sink-b','name':'シンクB'}]}]}}]}}
    before=deepcopy((review,questions));result=build_lunch_review(review,questions)
    assert [row['食材'] for row in result]==['にんじん','じゃがいも']
    assert [row['使用設備'] for row in result]==['シンクA','シンクB']
    assert all(row['調理方法']=='別々に調理' and row['担当']=='担当A' for row in result)
    assert (review,questions)==before
    step=review['evidence'][0]['data']['document']['steps'][0]
    step['daily_work']={'mode':'together','placements':[{'ingredient_ids':['c','p'],'equipment':{'id':'sink-a'}}]}
    step.pop('execution')
    result=build_lunch_review(review,questions)
    assert len(result)==1 and result[0]['食材']=='にんじん・じゃがいも'
    assert result[0]['開始']=='未入力' and result[0]['担当']=='未入力'
    assert build_lunch_review(None,questions) is None

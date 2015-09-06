var todoListAPI = require('./todoListAPI')
var api = todoListAPI.api


function add_item(items) {
    var item = new api.Item({'description':'buy milk'});
}
// rslt = api.retrieve_well_known_resource('http://localhost:3000/message')
rslt = api.retrieve('http://localhost:3001/to-dos', function(error, todoList) {
    if (error) {
        console.log(error)
    } else {
        if (!(todoList instanceof todoListAPI.TodoList)) throw 'assert';
        todoList.retrieve('items', function(error, entity) {
            if (error) {
                console.log('error', error)
            } else {
                console.log(Object.getPrototypeOf(entity).className)
                console.log('entity', entity)
            }
        })
    }
})
/*
from rapier.test.python_sdk.todo_list.todo_list_api import api, Item, Collection, TodoList

def test_objects():
    todo_list = api.retrieve_well_known_resource('http://localhost:3001/to-dos')
    items = todo_list.retrieve('items')
    new_item = Item({'description':'buy milk'})
    items.create(new_item)
    assert(hasattr(new_item, '_location') and new_item._location)                
    items.refresh()
    assert(new_item._location in items.items)
    new_item.description = 'buy more milk'
    new_item.due = 'tonight'
    new_item.update()
    new_item.refresh()
    assert(new_item.description == 'buy more milk')
    new_item.delete()
    items.refresh()
    assert(new_item._location not in items.items)
    
def test_api():
    todo_list = api.retrieve('http://localhost:3001/to-dos')
    todo_list.refresh()
    assert(hasattr(todo_list,'items'))
    items = api.retrieve('http://localhost:3001/to-dos/items')
    new_item = Item({'description':'buy milk'})
    items.create(new_item)
    assert(hasattr(new_item, '_location') and new_item._location)                
    items.refresh()
    assert(new_item._location in items.items)
    new_item2 = items.items[new_item._location]
    new_item2.description = 'buy more milk'
    new_item2.due = 'tonight'
    new_item2.update()
    assert(new_item2._etag == str(int(new_item._etag) + 1))
    assert(new_item2.description == 'buy more milk')
    new_item3 = api.delete(new_item._location)
    assert(new_item3._etag == new_item2._etag)
    items.refresh()
    assert(new_item3._location not in items.items)
    
def main():
    test_objects()
    test_api()

if __name__ == '__main__':
    main()
*/
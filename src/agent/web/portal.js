document.addEventListener('DOMContentLoaded', function() {
    fetch('/tasks?limit=5')
        .then(response => response.json())
        .then(tasks => {
            const tasksList = document.getElementById('tasks-list');
            tasks.forEach(task => {
                const taskItem = document.createElement('a');
                taskItem.href = task.pr_url || '#';
                taskItem.className = 'list-group-item list-group-item-action';
                taskItem.textContent = `${task.intent} - ${task.status}`;
                tasksList.appendChild(taskItem);
            });
        })
        .catch(error => {
            console.error('Error fetching tasks:', error);
            const tasksList = document.getElementById('tasks-list');
            const errorItem = document.createElement('div');
            errorItem.className = 'alert alert-danger';
            errorItem.textContent = 'Failed to load tasks. Please try again later.';
            tasksList.appendChild(errorItem);
        });
});
